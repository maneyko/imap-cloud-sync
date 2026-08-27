# uploader

Pulls mail from IMAP and writes it to S3. Append-only, resumable, and safe to
interrupt at any moment.

```bash
./main.py                              # every account under /etc/imap-cloud-sync/secrets
./main.py me@example.com you@example.com
```

One process syncs every account, one account at a time, then exits. Run it from
a timer (`etc/systemd/`) or by hand.

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

## Configuration

One toml per account in `/etc/imap-cloud-sync/secrets/`, named for the address.
Only the IMAP block is required; everything else has a default in
`lib/config.py`.

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
pushed after every batch. A run resumes from there.

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

## One-off tools

Neither is part of normal operation; both are kept because the next account
migration will want them.

**`import_nas.py`** — backfills mail that predates the IMAP sync from a maildir
on disk, joined against a sqlite metadata DB by maildir basename. It assigns
synthetic UIDs numbered `1..N` in date order (which stay below the real UIDs
the mailbox resumes from) and writes the identical object/sidecar pair, so the
archiver cannot tell imported mail from live mail. Used to import 382,752
messages.

**`upload_from_csv.py`** — uploads the files listed in a CSV
(`uid,key,internaldate,path`). Used to recover messages that existed only in a
local maildir because they had been removed from the IMAP mailbox before the
sync could see them.

Both take `DRY_RUN=1` and `LIMIT=n`, and both are resumable through a `.done`
log. Neither touches `state.json` — synthetic UIDs must never leak into the
checkpoint, or the next sync will resume from the wrong place.

## Gmail notes

- Sync `[Gmail]/All Mail`: it is a superset of INBOX and Sent, so syncing all
  three stores everything twice.
- UID order does not follow date order. A bulk re-label rewrites UIDs, so a sync
  can appear to walk backwards through the years. Keys are date-based, so this
  is cosmetic.
- Archiving a message in Gmail removes it from INBOX, and an INBOX-only sync
  cannot see it again — another reason to prefer All Mail.
