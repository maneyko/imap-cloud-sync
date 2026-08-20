#!/usr/bin/env -S PYTHONUNBUFFERED=1 uv run --script

# /// script
# dependencies = ["boto3"]
# requires-python = ">=3.14"
# ///

"""Sync every configured mailbox to S3, then exit.

Run it directly with:
    EMAIL_ADDRESSES=me@example.com,you@example.com ./main.py
"""

import os
import sys
import traceback

from lib.config import SECRETS_DIR
from lib.sync import Sync


def configured_addresses() -> list[str]:
    overrides = sum((os.environ.get(key, "").split(",") for key in ["EMAIL", "EMAILS", "EMAIL_ADDRESSES"]), [])
    overrides += sys.argv[1:]
    overrides = [v for v in overrides if v]

    if len(overrides) > 0:
        return overrides
    return sorted(path.name.removesuffix(".toml") for path in SECRETS_DIR.glob("*.toml"))


def main() -> int:
    addresses = configured_addresses()
    failed = []
    for name in addresses:
        try:
            Sync(name).run()
        except Exception:
            traceback.print_exc()
            failed.append(name)

    if failed:
        print(f"ERROR: Sync failed for: {failed}")
        return 1

    print(f"SUCCESS: Synced emails: {addresses}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
