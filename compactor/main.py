#!/usr/bin/env -S PYTHONUNBUFFERED=1 uv run --script

# /// script
# dependencies = ["boto3"]
# requires-python = ">=3.14"
# ///

"""Roll the hot "*.eml.zst" objects the uploader wrote into DEEP_ARCHIVE tars.

Per mailbox root "<address>/<mailbox>/"::

    .../email/2026/08/06/21-14-20.1786068860.uid-123456.eml.zst  <- source (STANDARD)
    .../metadata/2026/08/06/21-14-20.1786068860.uid-123456.json  <- left alone
    .../archive/archive-000042.tar                               <- bundle (DEEP_ARCHIVE)
    .../archive/archive-000042.manifest.jsonl.zst                <- contents (STANDARD)

Once a mailbox has MIN_ARCHIVE_BYTES of email, the oldest objects are streamed
into the next tar and then deleted, so "email/" only ever holds what has not
been archived yet. Settings live in lib/config.py; there are no arguments and
the Lambda event is ignored.

    AWS_PROFILE=personal ./main.py
"""

import json
import sys

from lib.archiver import Archiver


def lambda_handler(event=None, context=None):
    summary = Archiver(context).run()
    print(json.dumps(summary, indent=2, default=str))
    return summary


if __name__ == "__main__":
    lambda_handler()
    sys.exit(0)
