#!/usr/bin/env -S PYTHONUNBUFFERED=1 uv run --script

# /// script
# dependencies = ["boto3"]
# requires-python = ">=3.14"
# ///

"""Roll many small S3 objects into few large tars, then delete the originals.

The bucket to work on comes from $ARCHIVE_BUCKET; everything else is read from
s3://$ARCHIVE_BUCKET/bucket-archive/config.toml, so one deployment can serve
several buckets with different layouts::

    prefix_pattern = ['@', '.*', '^email$']   # one regex per level, top down
    suffix_pattern = '\\.eml\\.zst$'            # which objects to bundle

Each discovered prefix is archived into the matching path under
"bucket-archive/", leaving anything that does not match the suffix alone::

    me@example.com/INBOX/email/2026/08/06/21-14-20.uid-123456.eml.zst   <- source
    me@example.com/INBOX/metadata/2026/08/06/21-14-20.uid-123456.json   <- left alone
    bucket-archive/me@example.com/INBOX/email/archive-000042.tar        <- bundle
    bucket-archive/me@example.com/INBOX/email/archive-000042.manifest.jsonl.zst

Once a prefix holds min_archive_mib of matching objects, the oldest are streamed
into the next tar and then deleted, so a source prefix only ever holds what has
not been archived yet. There are no arguments and the Lambda event is ignored.

    ARCHIVE_BUCKET=my-mail-archive ./main.py
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
