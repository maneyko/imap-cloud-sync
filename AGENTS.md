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
  a toml beat flags, flags beat environment variables.

## Scope

This repo is the uploader only. The archiver that rolls these objects into
Deep Archive tars lives in
[bucket-archiver](https://github.com/maneyko/bucket-archiver); it knows nothing
about email, and that separation is deliberate. Anything about tars, manifests,
or `bucket-archive/` belongs there.

## Invariants — do not break these

1. **The uploader can never delete.** Its IAM key has no `DeleteObject`. Keep it
   that way; it is the reason a compromised client cannot destroy the archive.
2. **Metadata failure must never block ingestion.** The raw message is the
   record. If headers cannot be parsed, store the message with thin metadata and
   warn — a single malformed message must not wedge a mailbox forever.

## Sharp edges, all of which have already caused a bug

**IAM resources are scoped by suffix, not by path.** `*/email/*` broke silently
the moment the layout changed. The uploader's write policy now matches
`*.eml.zst` and `*.eml.zst.json`, which cannot accidentally match a tar, a
manifest, a `state.json`, or the config.

**`min_age_seconds` in the archiver is a race guard, not a nicety.** The
uploader writes the object and then its sidecar. If the archiver bundles in
between, that message loses its metadata permanently.

**S3 keys embed the UID, so a different UID is a duplicate, not an overwrite.**
This is how the historical import and the live sync can both hold the same
message. Dedupe on `Message-ID`, not on key.

**`imaplib._MAXLINE` caps a `UID SEARCH` reply at 1 MB**, which a 125k-message
mailbox exceeds. `MailClient.uids()` pages the search in windows derived from
that constant.

**A `uidvalidity` mismatch resets `last_processed_uid` to 0** and re-syncs the
entire mailbox. When hand-writing a `state.json`, the `uidvalidity` must match
the server exactly, and it is per *mailbox*, not per account.

**`systemctl disable` deletes a unit that is a symlink into the checkout.** The
role copies the units into `/etc/systemd/system` for that reason; linking them
means turning the timer off also removes it.

**This app reads a shared uv cache it does not own.** The shebang is `uv run
--script`, so uv builds an ephemeral environment under
`/opt/uv/cache/environments-v2` from the packages in `archive-v0`. This app has
no venv of its own and only ever *reads* that cache — which makes it the first
thing to break when another consumer of `/opt/uv` mishandles it, and it will
break as a plain `Permission denied` on some `.dist-info` file. Check the group
and mode on the file uv names before looking anywhere else, and check whether it
is shared with something outside the cache:

```sh
find /opt /home -xdev -inum $(stat -c %i <the file uv named>)
```

**The deploy role chowns its own checkout, which breaks root's git on the next
run** — `detected dubious ownership`. The first apply works because root created
the clone; every apply after it fails, unless something else on the host has
already set `safe.directory` globally, which is a dependency this role does not
declare and should not rely on. The fix is to scope the exception to the clone
task with `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0` in its
own `environment:`, leaving no state on the host. Not done here yet.

**Gmail:** `X-GM-*` fetch attributes are gated on the `X-GM-EXT-1` capability,
not on the address — Workspace domains serve them too, and a server without them
rejects the whole `FETCH` as `BAD`. Gmail UID order does not follow date order
(a re-label rewrites UIDs), so a sync can march *backwards* through time. IMAP
downloads are throttled to roughly 2.5 GB/day per account, which is why the
timer is daily and `max_download_mib` caps a single run.

## Testing

There are no unit tests, and adding a framework is not the answer. What has
worked:

- **Create a throwaway bucket** and exercise the real code path against it, then
  delete the bucket.
- **Sync one account at a time** (`./main.py me@example.com`) and cap it with
  `max_download_mib` so a change can be rehearsed on a small mailbox before it
  runs against a 125k-message one.
- **Verify by reading back from S3**, not by trusting the return code.
  Decompress a body, diff a listing against what you believe you uploaded.
- **Reconcile counts.** Nearly every real bug showed up as an arithmetic
  mismatch: files vs database rows, manifest entries vs distinct message-ids,
  uploaded objects vs `Total Objects`.

## Deploying

`ansible/` is a collection with one role, `maneyko.imap_cloud_sync.deploy`,
which is how this lands on a host. The caller passes exactly two variables,
`config` and `secrets`, and holds no knowledge of the layout — not the paths,
not the unit names, not the fact that accounts are tomls. Keep it that way: if
the calling repo has to learn something new about this app, the role is missing
a task.

The account tomls name their own files: the role reads `imap.username` back out
of each document, so the secret needs no keys alongside it.

## Repo map

```
ansible/
  galaxy.yml           collection metadata; consumed as maneyko.imap_cloud_sync
  roles/deploy/        uv, user, clone, secrets, AWS creds, systemd timer

main.py                entry point; one process, all accounts, exits when done
lib/sync.py            the per-mailbox loop and checkpointing
lib/mail_client.py     IMAP plumbing, reconnects, UID paging, capability detection
lib/email.py           one message: parsing, metadata, compression
lib/state.py           per-mailbox checkpoint stored in S3
lib/config.py          per-account toml merged over the defaults
etc/systemd/           the units the role installs into /etc/systemd/system
```

Account secrets live in `/etc/imap-cloud-sync/secrets/<address>.toml`, on the
host and on a laptop alike; symlink that path at a scratch directory to work on
a checkout.
