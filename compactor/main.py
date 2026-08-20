#!/usr/bin/env -S uv run --script

# /// script
# dependencies = ["boto3"]
# requires-python = ">=3.14"
# ///

"""S3 archiver Lambda entry point.

Rolls the individual hot ``*.eml.zst`` objects produced by ``main.py`` into
~250 MiB ``.tar`` bundles stored as DEEP_ARCHIVE, then deletes the objects that
were bundled. No IMAP credentials are needed or used here: the Lambda only
talks to S3.

Layout it works on (per mailbox root ``<address>/<mailbox>/``)::

    me@example.com/INBOX/email/2026/08/06/21-14-20.1786068860.uid-123456.eml.zst  <- source (STANDARD)
    me@example.com/INBOX/metadata/2026/08/06/21-14-20.1786068860.uid-123456.json  <- left alone
    me@example.com/INBOX/archive/archive-000042.tar                               <- bundle (DEEP_ARCHIVE)
    me@example.com/INBOX/archive/archive-000042.manifest.jsonl.zst                <- contents (STANDARD)
    me@example.com/INBOX/archive-state.json                                       <- checkpoint

Algorithm (per mailbox root):
  1. List objects after ``last_archived_key``
  2. Accumulate pending_bytes / pending_objects
  3. Once pending_bytes >= target: create archive-000042.tar, stream-upload it,
     update state, delete the source objects.

Run locally against the real bucket (read-only first!)::

    AWS_PROFILE=personal ./main.py --dry-run
    AWS_PROFILE=personal ./main.py --prefix 'me@example.com/INBOX/'

Suggested Lambda config: 512-1024 MB memory, 15 min timeout, EventBridge daily
schedule, plus a lifecycle rule aborting incomplete multipart uploads after a
day or two.
"""

import json
import sys

from lib import ArchiveConfig, Archiver


def lambda_handler(event=None, context=None):
    config = ArchiveConfig.from_event(event)
    summary = Archiver(config, context).run()
    print(json.dumps(summary, indent=2, default=str))
    return summary


def main(argv):
    """Tiny CLI wrapper so the same code can be exercised from a laptop."""
    event = {}
    args = list(argv)
    while args:
        arg = args.pop(0)
        match arg:
            case "--dry-run":
                event["dry_run"] = True
            case "--force":
                event["force"] = True
            case "--no-delete":
                event["delete_sources"] = False
            case "--prefix":
                event.setdefault("prefixes", []).append(args.pop(0))
            case "--bucket":
                event["bucket"] = args.pop(0)
            case "--target-mib":
                mib = int(args.pop(0)) * 1024 * 1024
                event["target_bytes"] = mib
                event["min_bytes"] = mib
            case "--max-archives":
                event["max_archives_per_run"] = int(args.pop(0))
            case "-h" | "--help":
                print(__doc__)
                return 0
            case unknown:
                print(f"Unknown argument: {unknown}\n{__doc__}")
                return 2

    lambda_handler(event, None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
