#!/usr/bin/env -S uv run
# /// script
# dependencies = ["boto3"]
# ///

# exec(open("sync.py").read())
# from sync import EmailAddress, SyncMailbox; addr = EmailAddress("me@example.com"); mbox = SyncMailbox(addr)

from functools import cached_property
import json
import re
import email
from pathlib import Path

from lib.email import EmailRFC822
from lib.mail_client import MailClient
from lib.state import State

class EmailAddress:
    def __init__(self, email_address):
        self.name = email_address
        self.client = MailClient(email_address)
        self.config = self.client.config
        self.as_path = Path(email_address)

class Sync:
    def __init__(self, email_address: EmailAddress):
        pass

    def sync_all(self):
        pass


class SyncMailbox:
    def __init__(self, email_address: EmailAddress, mailbox="INBOX"):
        self.email_address = email_address
        self.mailbox = mailbox
        self.client = email_address.client
        self.config = self.client.config
        self.state = State(self.config, mailbox)

    def run(self):
        self.client.call("SELECT", self.mailbox, readonly=True)
        uids = self.client.uids(self.state.last_processed_uid())

        for event, mail in self.loop_uids(uids):
            if event == "email":
                print("Processing", mail.uid)
                self.write_to_dest(mail)
                self.update_local_state(mail)
            elif event == "batch_complete":
                self.state.push_to_remote()

    @cached_property
    def mailbox_s3_name(self):
        return self.mailbox

    def write_to_dest(self, mail: EmailRFC822):
        stem_path = mail.internaldate.strftime(self.config.path_template.format(epoch=mail.epoch, uid=mail.uid))
        mbox_path = self.email_address.as_path / self.mailbox_s3_name

        mail_path = mbox_path / "email" / stem_path
        meta_path = mbox_path / "metadata" / stem_path

        [d.parent.mkdir(parents=True, exist_ok=True) for d in (mail_path, meta_path)]

        mail_path.with_suffix(".eml.zst").write_bytes(mail.body_compressed)
        meta_path.with_suffix(".json").write_text(json.dumps(mail.metadata))

    def update_local_state(self, mail: EmailRFC822):
        self.state.message_count(self.state.message_count() + 1)
        self.state.uncompressed_bytes(self.state.uncompressed_bytes() + len(mail.body))
        self.state.compressed_bytes(self.state.compressed_bytes() + len(mail.body_compressed))
        self.state.last_processed_uid(mail.uid)

    def loop_uids(self, uids: list[bytes]):
        for i in range(0, len(uids), self.config.batch_size):
            group = uids[i:i+self.config.batch_size]
            uid_range = (group[0] + b":" + group[-1]).decode()
            data = self.client.call("UID", "FETCH", uid_range, "(UID INTERNALDATE BODY[])")
            for item in data:
                if not isinstance(item, tuple):
                    continue
                yield "email", EmailRFC822(self.config, *item)
            yield "batch_complete", None

# while True:
#     connect()
#     uids = get_next_uids(last_uid)
#     for batch in batches(uids, size=50):
#         fetch(batch)
#         store_emails(batch)
#         update_checkpoint(batch)
#     logout()
