#!/usr/bin/env -S uv run
# /// script
# dependencies = ["boto3"]
# requires-python = ">=3.14"
# ///
"""Sync every configured mailbox to S3, then exit.

This is the entry point used by the imap-sync systemd service (a oneshot unit
driven by imap-sync.timer). main.py holds the sync logic but is import-only;
this module just decides *what* to sync and reports failures via the exit code
so that systemd marks a bad run as failed.

Which addresses run:
  * $IMAP_SYNC_ADDRESSES, comma or newline separated, if set; otherwise
  * every secrets/<address>.toml in the repo.

Which mailboxes run: the intersection of the mailboxes the server advertises
and the `mailboxes` list in that address's imap config.

Run it directly with:
    ./sync_all.py
    IMAP_SYNC_ADDRESSES=me@example.com ./sync_all.py
"""

import os
import sys
import traceback

from lib.config import SECRETS_DIR
from lib.sync_mailbox import EmailAddress, SyncMailbox


def configured_addresses() -> list[str]:
    raw = os.environ.get("IMAP_SYNC_ADDRESSES", "")
    if raw.strip():
        return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]
    return sorted(path.name.removesuffix(".toml") for path in SECRETS_DIR.glob("*.toml"))


def mailboxes_for(address: EmailAddress) -> list[str]:
    """Server mailboxes that this address is configured to sync."""
    wanted = address.config.imap["mailboxes"]
    advertised = [mailbox["name"].decode() for mailbox in address.client.mailboxes]
    return [name for name in advertised if name in wanted]


def validate_unique_s3_names(mailbox_names: list[str]) -> None:
    """Two IMAP names must never collapse to the same S3 prefix."""
    s3_names = [SyncMailbox.get_mailbox_s3_name(name) for name in mailbox_names]
    if len(set(s3_names)) != len(set(mailbox_names)):
        mapping = dict(zip(mailbox_names, s3_names))
        raise RuntimeError(f"Mailbox names for S3 are not unique: {mapping}")


def sync_address(name: str) -> None:
    print(f"=== {name}", flush=True)
    address = EmailAddress(name)
    try:
        mailbox_names = mailboxes_for(address)
        validate_unique_s3_names(mailbox_names)
        if not mailbox_names:
            print(f"WARNING: no configured mailboxes matched for {name}", flush=True)
        for mailbox in mailbox_names:
            print(f"--- {name} {mailbox}", flush=True)
            SyncMailbox(address, mailbox).run()
    finally:
        try:
            address.client.logout()
        except Exception:
            pass


def main() -> int:
    addresses = configured_addresses()
    if not addresses:
        print(f"ERROR: no addresses configured (looked in {SECRETS_DIR})", file=sys.stderr)
        return 1

    failed = []
    for name in addresses:
        try:
            sync_address(name)
        except Exception:
            # Keep going: one broken mailbox should not stop the others.
            traceback.print_exc()
            failed.append(name)

    if failed:
        print(f"ERROR: sync failed for: {', '.join(failed)}", file=sys.stderr)
        return 1

    print(f"Synced {len(addresses)} address(es) successfully", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
