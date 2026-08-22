# imap-cloud-sync

Personal mail archive. Pulls mail off IMAP servers, stores each message in S3 as
a compressed object with a metadata sidecar, then rolls those small objects into
large tarballs in Glacier Deep Archive.

Two components, deliberately independent:

| | runs | job |
|---|---|---|
| [`uploader/`](uploader/) | on a machine you own, on a timer | IMAP → S3. Append-only, resumable. |
| [`compactor/`](compactor/) | AWS Lambda, nightly | many small S3 objects → few large tars in Deep Archive |

They share nothing but the bucket layout. The uploader has no idea archives
exist; the compactor has no idea what an email is.

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
   IMAP                    S3 STANDARD                      S3 DEEP_ARCHIVE
 ┌────────┐   uploader   ┌──────────────────────┐  compactor  ┌────────────────┐
 │ INBOX  │ ───────────► │ <addr>/<mailbox>/... │ ──────────► │ bucket-archive │
 │ Sent   │              │   *.eml.zst          │             │  archive-N.tar │
 │ AllMail│              │   *.eml.zst.json     │             └────────────────┘
 └────────┘              │   state.json         │                     │
                         └──────────────────────┘             manifest stays
                              sources deleted                 in STANDARD, and
                              once inside a tar               is also inside the tar
```

## Bucket layout

```
me@example.com/INBOX/state.json                                   sync checkpoint
me@example.com/INBOX/2026/08/06/21-14-20.1786068860.uid-123.eml.zst      message
me@example.com/INBOX/2026/08/06/21-14-20.1786068860.uid-123.eml.zst.json metadata
bucket-archive/config.toml                                        compactor settings
bucket-archive/me@example.com/INBOX/archive-000001.tar            DEEP_ARCHIVE
bucket-archive/me@example.com/INBOX/archive-000001.manifest.jsonl.zst   STANDARD
```

Object keys are derived from the message date and its IMAP UID, so they sort
chronologically and are stable — re-uploading a message overwrites it rather
than duplicating it.

## Three ideas hold the whole thing together

**The bucket is the state.** The compactor keeps no checkpoint file. Sources are
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
# 1. one toml per account
cat > uploader/secrets/me@example.com.toml <<'EOF'
[imap]
server   = "imap.example.com"
username = "me@example.com"
password = "..."
EOF

# 2. sync (idempotent, resumable, safe to ctrl-c)
cd uploader && ./main.py me@example.com

# 3. tell the compactor how this bucket is laid out
cat > /tmp/config.toml <<'EOF'
prefix_pattern = ['@', '.*']
suffix_pattern = '\.eml\.zst$'
EOF
aws s3 cp /tmp/config.toml s3://BUCKET/bucket-archive/config.toml

# 4. archive
cd compactor && ARCHIVE_BUCKET=BUCKET ./main.py
```

Both components run under `uv` with inline dependencies — no virtualenv to
manage, no requirements file. They need Python 3.14 for the stdlib
`compression.zstd` module.

## Infrastructure

Terraform lives in a separate repo (`terraform-aws`): the bucket, the Lambda,
its schedule, and two tightly-scoped IAM identities. The uploader may only
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

**The compactor is intended to move to its own repository.** Nothing in it knows
about email — it discovers prefixes with a list of regexes, bundles objects
matching a suffix, and carries along a metadata sidecar whose contents it never
inspects. That genericity is deliberate: the next use is **photo and video
backup**, where the layout is `2026/08/12-00-34.567.jpg` with an
`exiftool -json` sidecar at `2026/08/12-00-34.567.jpg.json`. The only change
required is a different `bucket-archive/config.toml`:

```toml
prefix_pattern = ['^\d{4}$', '^\d{2}$']
suffix_pattern = '\.(jpe?g|png|mp4|mov)$'
```

Deciding what metadata *means* stays with whichever uploader writes it. Keeping
that line clean is what lets one archiver serve both buckets.
