# Working in this repo

Notes for whoever picks this up next, human or agent. The code is small and
should stay that way; most of what is worth knowing is *why* it is shaped this
way and which sharp edges have already drawn blood.

## Style

- Python 3.14, run via `uv` script shebangs with inline dependencies. No
  virtualenv, no requirements file.
- Small modules, plain functions and classes, no frameworks.
- Prefer deleting code to adding abstraction. This codebase went from 812 lines
  to under 400 by removing options nobody used; keep it there.
- Comments explain *why*, never *what*. If a comment restates the line below it,
  delete the comment and fix the name.
- No config option gets added until a second real caller needs it. Constants in
  a toml beat flags, flags beat environment variables, and the compactor takes
  no environment at all — the invocation names the bucket (`argv[1]` on the CLI,
  `{"bucket": ...}` in the Lambda event) and the bucket names everything else.

## Invariants — do not break these

1. **The compactor keeps no checkpoint.** Whatever remains under a source prefix
   is what still needs archiving. Never add a state file "for speed".
2. **Delete only after the tar is committed.** The order is: write tar → write
   manifest → delete sources. A crash anywhere leaves duplicate work, never data
   loss.
3. **The tar is self-sufficient.** Sidecars and the manifest go inside it. This
   is what makes the manifest disposable and regenerable.
4. **The uploader can never delete.** Its IAM key has no `DeleteObject`. Keep it
   that way; it is the reason a compromised client cannot destroy the archive.
5. **Metadata failure must never block ingestion.** The raw message is the
   record. If headers cannot be parsed, store the message with thin metadata and
   warn — a single malformed message must not wedge a mailbox forever.
6. **One writer at a time.** The compactor assumes nothing else is mutating the
   bucket. EventBridge retries are disabled for this reason.

## Sharp edges, all of which have already caused a bug

**IAM resources are scoped by suffix, not by path.** `*/email/*` broke silently
the moment the layout changed, twice — once for the Lambda's deletes and once
for the uploader's writes. Policies now match `*.eml.zst` and `*.eml.zst.json`,
which cannot accidentally match a tar, a manifest, a `state.json`, or the config.
Failures here are silent: deletes come back in `delete_errors`, not as an
exception.

**`zip -r` appends to an existing archive.** `bin/deploy.sh` removes the zip
first, or you ship files you deleted months ago.

**`aws lambda invoke` retries after 60 seconds.** The CLI's default read timeout
silently fires a *second concurrent invocation*, which then races the first for
the same objects and dies with `NoSuchKey`. Always pass `--cli-read-timeout 0`.

**`min_age_seconds` is a race guard, not a nicety.** The uploader writes the
object and then its sidecar. If the compactor archives in between, that message
loses its metadata permanently. Keep it at 3600.

**Deep Archive objects cannot be copied or renamed.** `CopyObject` fails with
`InvalidObjectState` until restored (12–48 h). Get the naming right before
writing, because you cannot fix it afterwards. Deleting early still bills the
180-day minimum.

**S3 keys embed the UID, so a different UID is a duplicate, not an overwrite.**
This is how the historical import and the live sync can both hold the same
message. Dedupe on `Message-ID`, not on key.

**`imaplib._MAXLINE` caps a `UID SEARCH` reply at 1 MB**, which a 125k-message
mailbox exceeds. `MailClient.uids()` pages the search in windows derived from
that constant.

**A `uidvalidity` mismatch resets `last_processed_uid` to 0** and re-syncs the
entire mailbox. When hand-writing a `state.json`, the `uidvalidity` must match
the server exactly, and it is per *mailbox*, not per account.

**Gmail:** `X-GM-*` fetch attributes are gated on the `X-GM-EXT-1` capability,
not on the address — Workspace domains serve them too, and a server without them
rejects the whole `FETCH` as `BAD`. Gmail UID order does not follow date order
(a re-label rewrites UIDs), so a sync can march *backwards* through time. IMAP
downloads are throttled to roughly 2.5 GB/day per account.

## Testing

There are no unit tests, and adding a framework is not the answer. What has
worked:

- **Create a throwaway bucket** and exercise the real code path against it, then
  delete the bucket. Every compactor change was validated this way, including
  the edge cases (missing sidecar, corrupt sidecar, `state.json` sitting inside a
  source prefix).
- **`DRY_RUN=1` and `LIMIT=n`** exist on the one-off import scripts so a real run
  can be rehearsed and then sampled before committing to 400k objects.
- **Verify by reading back from S3**, not by trusting the return code. Round-trip
  a tar, decompress a body, diff a manifest against the bucket listing.
- **Reconcile counts.** Nearly every real bug showed up as an arithmetic
  mismatch: files vs database rows, manifest entries vs distinct message-ids,
  uploaded objects vs `Total Objects`.

## Deploying

```bash
cd compactor && ./bin/deploy.sh     # builds the zip, uploads it, updates the Lambda
```

Terraform lives in the `terraform-aws` repo and pins the package version, so a
`terraform apply` will roll the function back to whatever version is recorded
there. Bump it, or accept that the CLI deploy is temporary.

The uploader has no deploy step — copy the directory to the host that runs it,
along with `secrets/`. `etc/systemd/` has a timer unit for that.

## Repo map

```
uploader/
  main.py              entry point; one process, all accounts, exits when done
  import_nas.py        one-off: maildir + sqlite metadata -> S3 (historical backfill)
  upload_from_csv.py   one-off: upload files listed in a CSV (orphan recovery)
  lib/sync.py          the per-mailbox loop and checkpointing
  lib/mail_client.py   IMAP plumbing, reconnects, UID paging, capability detection
  lib/email.py         one message: parsing, metadata, compression
  lib/state.py         per-mailbox checkpoint stored in S3
  secrets/*.toml       one per account (gitignored)

compactor/
  main.py              Lambda handler and CLI, same code path
  config.toml          defaults; the bucket's own config overrides them
  lib/archiver.py      prefix discovery, selection, the archive loop
  lib/tar_builder.py   streams objects + sidecars into a tar, writes the manifest
  lib/s3util.py        S3 wrapper and the multipart upload stream
  bin/deploy.sh        build, upload, update function code
```
