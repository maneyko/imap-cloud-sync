# imap-cloud-sync

Personal mail archive. Pulls mail off IMAP servers and stores each message in S3
as a compressed object with a metadata sidecar. Append-only, resumable, and safe
to interrupt at any moment.

```bash
./main.py                              # every account in /etc/imap-cloud-sync/secrets
./main.py me@example.com you@example.com
```

One process syncs every account, one account at a time, then exits. Run it from
the timer in [`etc/systemd/`](etc/systemd/) or by hand.

The second half of the story lives elsewhere: those small objects are rolled
into large tarballs in Glacier Deep Archive by
[bucket-archiver](https://github.com/maneyko/bucket-archiver), a Lambda that has
no idea what an email is. This repo has no idea archives exist. They share
nothing but the bucket layout.

## Why it is shaped this way

Storing 400,000 emails as 400,000 Deep Archive objects does not work: each one
carries 40 KB of billable metadata overhead and costs a request to restore. So
mail lands in STANDARD as individual objects, where it is cheap to write and
easy to inspect, and a second process periodically packs it into ~250 MiB tars
that move to Deep Archive. At that size the overhead disappears and storage
costs roughly **$1 per TB per month**.

The split also means the risky part (deleting things) lives in one place, in a
Lambda whose IAM role cannot touch anything but source objects.

## Data flow

```
   IMAP                    S3 STANDARD                    S3 DEEP_ARCHIVE
 ┌────────┐   this repo  ┌──────────────────────┐ bucket-  ┌────────────────┐
 │ INBOX  │ ───────────► │ <addr>/<mailbox>/... │ archiver │ bucket-archive │
 │ Sent   │              │   *.eml.zst          │ ───────► │  archive-N.tar │
 │ AllMail│              │   *.eml.zst.json     │          └────────────────┘
 └────────┘              │   state.json         │                  │
                         └──────────────────────┘          manifest stays
                              sources deleted              in STANDARD, and
                              once inside a tar            is also inside the tar
```

## What it writes

For every message, two objects side by side:

```
me@example.com/INBOX/2026/08/06/21-14-20.1786068860.uid-123456.eml.zst
me@example.com/INBOX/2026/08/06/21-14-20.1786068860.uid-123456.eml.zst.json
```

The key is `<address>/<mailbox>/<internaldate path>.<epoch>.uid-<uid>.eml.zst`.
Because it is derived entirely from the message, re-uploading is an exact
overwrite — never a duplicate. That property is what makes interruption cheap.

The `.json` sidecar is the metadata: dates, sizes, message-id, subject, and the
address headers with display names. It sits next to the object rather than in a
parallel tree so the archiver can find it by appending a suffix, with no
knowledge of the layout.

```json
{
  "internaldate": "2026-08-06T16:14:20-05:00",
  "size": {"uncompressed": 191590, "compressed": 28019},
  "gmail": {"msgid": "1234567890123456789", "thrid": "1234567890123456789"},
  "message_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890@example.com",
  "date": "2026-08-02T07:02:48-05:00",
  "subject": "Ends today: Up to 75% off new markdowns",
  "from": [{"email_address": "sender@example.com", "display_name": "Example"}],
  "to": [{"email_address": "me@example.com"}]
}
```

The `gmail` block appears only for servers advertising `X-GM-EXT-1`. The ids are
strings because they are 64-bit and `jq` would round them as numbers.

The full bucket, once the archiver has been through it:

```
me@example.com/INBOX/state.json                                   sync checkpoint
me@example.com/INBOX/2026/08/06/21-14-20.1786068860.uid-123.eml.zst      message
me@example.com/INBOX/2026/08/06/21-14-20.1786068860.uid-123.eml.zst.json metadata
bucket-archive/config.toml                                        archiver settings
bucket-archive/me@example.com/INBOX/archive-000001.tar            DEEP_ARCHIVE
bucket-archive/me@example.com/INBOX/archive-000001.manifest.jsonl.zst   STANDARD
```

## Configuration

One toml per account in `/etc/imap-cloud-sync/secrets/`, named for the address.
Only the IMAP block is required; everything else has a default in
[`lib/config.py`](lib/config.py).

```toml
[imap]
server   = "imap.example.com"
username = "me@example.com"
password = "..."
# port = 993
# mailboxes = ['"INBOX"', 'INBOX', '"[Gmail]/All Mail"', '"[Gmail]/Sent Mail"', '"Sent Items"']

[processing]
# batch_size = 5            messages per FETCH, and per checkpoint
# max_download_mib = 1024   ceiling on what this account pulls per run

[storage]
# bucket_name = "my-mail-archive"
# timezone = "America/Chicago"
```

`mailboxes` is an allow-list intersected with what the server reports, so the
same default works for Gmail and non-Gmail accounts.

`max_download_mib` bounds each run. It exists because a first sync of a Gmail
All Mail folder is ~14 GiB and Google throttles IMAP to roughly 2.5 GB/day —
so the backfill is meant to take many runs. The budget is checked at batch
boundaries, so it always stops on a written checkpoint.

## Checkpoints

`<address>/<mailbox>/state.json` holds `last_processed_uid` and `uidvalidity`,
pushed after every batch. A run resumes from there. There is no local state: the
bucket is the record.

Two things to know before editing one by hand:

- `uidvalidity` must match the server, or the uploader treats the mailbox as
  reset and re-syncs from UID 0. It differs per mailbox, not per account.
- The uploader fetches from `last_processed_uid + 1`, so to start at UID *n*,
  store `n - 1`.

## Interrupts

`SIGINT`/`SIGTERM` set a flag; the sync stops at the next batch boundary, pushes
its checkpoint, logs out cleanly, and exits `128 + signum`. A second signal
aborts immediately. Worst case on a hard kill is re-fetching one batch, since
uploads are idempotent.

## Gmail notes

- Sync `[Gmail]/All Mail`: it is a superset of INBOX and Sent, so syncing all
  three stores everything twice.
- UID order does not follow date order. A bulk re-label rewrites UIDs, so a sync
  can appear to walk backwards through the years. Keys are date-based, so this
  is cosmetic.
- Archiving a message in Gmail removes it from INBOX, and an INBOX-only sync
  cannot see it again — another reason to prefer All Mail.

## Getting started

```bash
# 1. one toml per account, named for the address
sudo mkdir -p /etc/imap-cloud-sync/secrets
cat | sudo tee /etc/imap-cloud-sync/secrets/me@example.com.toml <<'EOF'
[imap]
server   = "imap.example.com"
username = "me@example.com"
password = "..."
EOF

# 2. sync (idempotent, resumable, safe to ctrl-c)
./main.py me@example.com

# 3. tell the archiver how this bucket is laid out
cat > /tmp/config.toml <<'EOF'
prefix_pattern = ['@', '.*']
suffix_pattern = '\.eml\.zst$'
EOF
aws s3 cp /tmp/config.toml s3://BUCKET/bucket-archive/config.toml
```

Archiving itself is `bucket-archiver`'s job; see that repo.

This runs under `uv` with inline dependencies — no virtualenv to manage, no
requirements file. It needs Python 3.14 for the stdlib `compression.zstd`
module.

## Deploying it

[`ansible/`](ansible/) is a collection holding one role,
`maneyko.imap_cloud_sync.deploy`, which puts all of the above on a host: `uv`, a
system user, a clone at `/opt/imap-cloud-sync`, the account tomls, AWS
credentials, and the systemd timer. A caller supplies only its settings and its
secrets:

```yaml
- hosts: all
  roles:
    - role: maneyko.imap_cloud_sync.deploy
      vars:
        config:  "{{ app_config }}"
        secrets: "{{ app_secrets }}"
```

The repo that owns the machine holds no imap-cloud-sync logic beyond that call —
it fetches the secret from wherever it keeps secrets and hands it over. See
[`ansible/README.md`](ansible/README.md).

## Terraform module

[`terraform/`](terraform/) is a module holding the only infrastructure this repo
owns: the IAM user the uploader runs as, its access key and its policy. The
bucket is not in here — it is shared with the archiver and with unrelated
archives, so the repo that owns it passes the ARN in.

```hcl
module "imap_sync_uploader" {
  source = "git::https://github.com/maneyko/imap-cloud-sync.git//terraform"

  mail_archive_bucket_arn = aws_s3_bucket.mail_archive.arn
}
```

The `access_key_id` and `secret_access_key` outputs are what the Ansible role
above installs on the host.

The IAM split is the part worth copying. Two identities touch the archive and
neither can do the other's job. The uploader may only `PutObject` on
`*.eml.zst` and
`*.eml.zst.json` and read/write `*/state.json` — it cannot delete anything, so a
stolen laptop key cannot destroy the archive. The Lambda from
[bucket-archiver](https://github.com/maneyko/bucket-archiver) may write only
under `bucket-archive/` and delete only source objects.

The source above tracks `main`; add `?ref=<tag>` to pin, and `terraform init
-upgrade` to pick up a change either way.

## Roughly what it costs

Measured on ~193k real messages (mean 76 KiB raw, ~0.5 compression ratio):

| | per account | 20 accounts |
|---|---|---|
| Deep Archive storage | ~$0.007/mo | ~$0.15/mo |
| Manifests (STANDARD) | ~$0.001/mo | ~$0.02/mo |
| One-time upload (PUTs) | ~$1.90 | ~$38 |

The recurring cost is negligible; the one-time request cost dominates, because
every message is two PUTs.
