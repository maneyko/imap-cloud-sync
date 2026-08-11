#!/usr/bin/env -S uv run
# /// script
# dependencies = ["boto3"]
# ///

# exec(open("main.py").read())
# from main import EmailAddress, SyncMailbox; addr = EmailAddress("me@example.com"); mbox = SyncMailbox(addr)

from functools import cached_property
import json
import re
import email
from pathlib import Path

from lib import EmailRFC822, MailClient, State

class EmailAddress:
    def __init__(self, email_address):
        self.name = email_address
        self.client = MailClient(email_address)
        self.config = self.client.config
        self.as_path = Path(email_address)

class Sync:
    def __init__(self, email_address: EmailAddress):
        self.email_address = email_address

    @cached_property
    def mailboxes(self):
        mailbox_names = [mbox["name"].decode() for mbox in self.email_address.client.mailboxes]
        return [mbox for mbox in mailbox_names if mbox in self.email_address.config.imap["mailboxes"]]

    def validate_unique_mailboxes(self):
        imap_names = [mbox["name"].decode() for mbox in self.mailboxes]
        s3_names = [SyncMailbox.get_mailbox_s3_name(name) for name in imap_names]

        if len(set(imap_names)) != len(set(s3_names)):
            mapping = {"imap_names": imap_names, "s3_names": s3_names}
            raise RuntimeError(f"Mailbox names for S3 are not unique: {json.dumps(mapping)}")

    def sync_all(self):
        self.validate_unique_mailboxes()
        # for mailbox in self.mailboxes:
        #     SyncMailbox(self.email_address, mailbox).run()


class SyncMailbox:
    @staticmethod
    def get_mailbox_s3_name(name):
        name = re.sub(r"['\"\[\]]", "", name)
        name = name.replace(" ", "_")
        name = re.sub(r"[^0-9a-zA-Z_-]", "-", name)
        return name

    def __init__(self, email_address: EmailAddress, mailbox="INBOX"):
        self.email_address = email_address
        self.mailbox = mailbox
        self.client = email_address.client
        self.config = self.client.config
        self.state = State(self.config, mailbox)
        self.store = self.config.store
        self.mailbox_s3_name = self.get_mailbox_s3_name(mailbox)

    def run(self):
        self.client.select(self.mailbox)

        uidvalidity = self.client.uidvalidity(self.mailbox)
        if self.state.uidvalidity() != uidvalidity:
            print("WARNING: uidvalidity has updated!")
            self.state.uidvalidity(uidvalidity)
            self.state.last_processed_uid(self.state.defaults["last_processed_uid"])

        uids = self.client.uids(self.state.last_processed_uid() + 1)
        for event, mail in self.loop_uids(uids):
            if event == "email":
                log_info = {
                    "email_address": self.email_address.name,
                    "mailbox": self.mailbox,
                    "uid": mail.uid,
                    "size_uncompressed": mail.size,
                    "size_compressed": mail.size_compressed,
                }
                print(f"Processing: {json.dumps(log_info)}")
                self.write_to_dest(mail)
                self.update_local_state(mail)
            elif event == "batch_complete":
                self.state.push_to_remote()

    def write_to_dest(self, mail: EmailRFC822):
        stem_path = mail.internaldate.strftime(self.config.path_template.format(epoch=mail.epoch, uid=mail.uid))
        mbox_path = self.email_address.as_path / self.mailbox_s3_name

        mail_path = mbox_path / "email" / stem_path
        meta_path = mbox_path / "metadata" / stem_path

        options = {"storage_class": self.config.storage["storage_class"]}
        if mail.size_compressed < self.config.s3_glacier_min_size:
            options["storage_class"] = "STANDARD"
        self.store.write(f"{mail_path}.eml.zst", mail.body_compressed, **options)
        self.store.write(f"{meta_path}.json", json.dumps(mail.metadata))

    def update_local_state(self, mail: EmailRFC822):
        self.state.message_count(self.state.message_count() + 1)
        self.state.uncompressed_bytes(self.state.uncompressed_bytes() + mail.size)
        self.state.compressed_bytes(self.state.compressed_bytes() + mail.size_compressed)
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
