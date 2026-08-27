# imap-cloud-sync

Personal mail archive. Pulls mail off IMAP servers and stores each message in S3
as a compressed object with a metadata sidecar.

The second half of the story lives elsewhere: those small objects are rolled
into large tarballs in Glacier Deep Archive by
[bucket-archiver](https://github.com/maneyko/bucket-archiver), a Lambda that has
no idea what an email is. This repo has no idea archives exist. They share
nothing but the bucket layout.

| | runs | job |
|---|---|---|
| [`uploader/`](uploader/) | on a machine you own, on a timer | IMAP → S3. Append-only, resumable. |
| `bucket-archiver` (separate repo) | AWS Lambda, nightly | many small S3 objects → few large tars in Deep Archive |

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
 ┌────────┐   uploader   ┌──────────────────────┐ bucket-  ┌────────────────┐
 │ INBOX  │ ───────────► │ <addr>/<mailbox>/... │ archiver │ bucket-archive │
 │ Sent   │              │   *.eml.zst          │ ───────► │  archive-N.tar │
 │ AllMail│              │   *.eml.zst.json     │          └────────────────┘
 └────────┘              │   state.json         │                  │
                         └──────────────────────┘          manifest stays
                              sources deleted              in STANDARD, and
                              once inside a tar            is also inside the tar
```

## Bucket layout

```
me@example.com/INBOX/state.json                                   sync checkpoint
me@example.com/INBOX/2026/08/06/21-14-20.1786068860.uid-123.eml.zst      message
me@example.com/INBOX/2026/08/06/21-14-20.1786068860.uid-123.eml.zst.json metadata
bucket-archive/config.toml                                        archiver settings
bucket-archive/me@example.com/INBOX/archive-000001.tar            DEEP_ARCHIVE
bucket-archive/me@example.com/INBOX/archive-000001.manifest.jsonl.zst   STANDARD
```

Object keys are derived from the message date and its IMAP UID, so they sort
chronologically and are stable — re-uploading a message overwrites it rather
than duplicating it.

## Three ideas hold the whole thing together

**The bucket is the state.** The archiver keeps no checkpoint file. Sources are
deleted once they are safely inside a tar, so whatever remains under a source
prefix is exactly what still needs archiving. Nothing to corrupt, nothing to get
out of sync, and `aws s3 ls` tells you the truth.

**The tar is self-sufficient.** Each message's metadata sidecar goes into the tar
next to it, and the manifest is written *inside* the tar as well as beside it. A
restored tarball can be understood with no config, no database and no manifest —
which is what makes the manifest safely disposable.

**The manifest is only an index.** It is a JSONL file, one line per message, with
the metadata inlined. It stays in STANDARD so you can answer "which bundle holds
this email?" without a Glacier restore, and it can be regenerated from the tar if
it is ever lost.

```bash
# who emailed me in 2025?
for k in $(aws s3 ls --recursive s3://BUCKET/bucket-archive/me@example.com/INBOX/ \
           | awk '$4 ~ /manifest\.jsonl\.zst$/ {print $4}'); do
  aws s3 cp "s3://BUCKET/$k" - --quiet | zstd -dc
done | jq -c 'select(.metadata.from[]?.email_address | test("bruce"))
              | {name, subject: .metadata.subject}'
```

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
cd uploader && ./main.py me@example.com

# 3. tell the archiver how this bucket is laid out
cat > /tmp/config.toml <<'EOF'
prefix_pattern = ['@', '.*']
suffix_pattern = '\.eml\.zst$'
EOF
aws s3 cp /tmp/config.toml s3://BUCKET/bucket-archive/config.toml
```

Archiving itself is `bucket-archiver`'s job; see that repo.

The uploader runs under `uv` with inline dependencies — no virtualenv to
manage, no requirements file. It needs Python 3.14 for the stdlib
`compression.zstd` module.

## Deploying it

[`ansible/`](ansible/) is a collection holding one role,
`maneyko.imap_cloud_sync.deploy`, which puts all of the above on a host: a
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

The repo that owns the machine (`google-setup`) holds no imap-cloud-sync logic
beyond that call — it pulls the secret out of GCP Secret Manager and hands it
over. See [`ansible/README.md`](ansible/README.md).

## Infrastructure

Terraform lives in a separate repo (`terraform-aws`): the bucket, the Lambda
(from `bucket-archiver`'s module), its schedule, and two tightly-scoped IAM
identities. The uploader may only
`PutObject` on `*.eml.zst` and `*.eml.zst.json` and read/write `*/state.json` —
it cannot delete anything, so a stolen laptop key cannot destroy the archive.
The Lambda may write only under `bucket-archive/` and delete only source objects.

## Roughly what it costs

Measured on ~193k real messages (mean 76 KiB raw, ~0.5 compression ratio):

| | per account | 20 accounts |
|---|---|---|
| Deep Archive storage | ~$0.007/mo | ~$0.15/mo |
| Manifests (STANDARD) | ~$0.001/mo | ~$0.02/mo |
| One-time upload (PUTs) | ~$1.90 | ~$38 |

The recurring cost is negligible; the one-time request cost dominates, because
every message is two PUTs.

## Future plans

Nothing pending. The archiver has moved to
[bucket-archiver](https://github.com/maneyko/bucket-archiver), where it also
serves a photo/video bucket. Deciding what metadata *means* stays here, with the
uploader that writes the sidecar; keeping that line clean is what lets one
archiver serve both buckets.
