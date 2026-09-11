#!/usr/bin/env -S PYTHONUNBUFFERED=1 uv run --script

# /// script
# dependencies = ["boto3"]
# requires-python = ">=3.14"
# ///

"""Export every configured mailbox to S3, then exit.

With no arguments every account in /etc/email-exporter/secrets is exported;
pass addresses to limit it. Each account stops once it has pulled its
max_download_mib.

    ./main.py
    ./main.py me@example.com you@example.com
"""

from collections import defaultdict
import os
import json
import sys
import traceback

from lib.config import SECRETS_DIR
from lib.export import Export
from lib.util import install_interrupt_handlers, interrupted


def configured_addresses() -> list[str]:
    if overrides := sys.argv[1:]:
        return overrides
    return sorted(path.name.removesuffix(".toml") for path in SECRETS_DIR.glob("*.toml"))


def main() -> int:
    install_interrupt_handlers()
    status = defaultdict(list)
    skipped = status["skipped"] = configured_addresses()
    while skipped and (name := skipped.pop()):
        if interrupted(): break
        try:
            code = Export(name).run()
            if code == 0:
                status["exported"].append(name)
            else:
                status["interrupted"].append(name)
        except Exception:
            traceback.print_exc()
            status["failed"].append(name)

    if failed := status.get("failed"):
        print(f"ERROR: Export failed: {json.dumps(status)}")
        return 1

    if interrupted():
        print(f"INTERRUPTED by {interrupted().name}: {json.dumps(status)}")
        return 128 + interrupted()

    print(f"SUCCESS: Exported emails: {json.dumps(status)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("ABORTED: interrupted twice, the last batch was not checkpointed")
        sys.exit(130)
