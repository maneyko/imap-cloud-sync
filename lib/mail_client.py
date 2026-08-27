from functools import cached_property
import imaplib
import re

from lib.config import Config

class MailClient:
    uid_batch_size = imaplib._MAXLINE // 16

    base_fetch_attributes = ["UID", "INTERNALDATE", "BODY[]"]
    gmail_fetch_attributes = ["X-GM-MSGID", "X-GM-THRID"]

    def __init__(self, email_address: str):
        self.email_address = email_address
        self.config = Config(self.email_address)
        self._current_mailbox = None
        self.create_connection()

    def create_connection(self):
        self.conn = imaplib.IMAP4_SSL(self.config.imap["server"], self.config.imap["port"])
        resp = self.login()[0].decode()
        print(f"Logged in to IMAP server {self.conn.host} as user {self.config.imap["username"]}: {resp}")
        if self._current_mailbox is not None:
            self.conn.select(self._current_mailbox[0], **self._current_mailbox[1])


    def call(self, func, *args, **kwargs):
        try:
            typ, data = getattr(self.conn, func.lower())(*args, **kwargs)
        except imaplib.IMAP4.abort:
            try:
                self.conn.logout()
            except Exception:
                pass
            self.create_connection()
            typ, data = getattr(self.conn, func.lower())(*args, **kwargs)
        if typ != "OK":
            raise RuntimeError(f"IMAP client returned unsuccessful response: typ={typ}, data={data}")
        return data

    @property
    def fetch_attributes(self) -> str:
        attributes = list(self.base_fetch_attributes)
        if "X-GM-EXT-1" in self.conn.capabilities:
            attributes += self.gmail_fetch_attributes
        return "(" + " ".join(attributes) + ")"

    def select(self, mailbox, **kwargs):
        kwargs = {"readonly": True} | kwargs
        self._current_mailbox = [mailbox, kwargs]
        return self.call("SELECT", mailbox, **kwargs)

    def uids(self, starting_uid: int, ending_uid=None) -> list[int]:
        "Return list of sorted UIDs from the specified bounds."
        ending_uid = int(ending_uid) if ending_uid else self.uidnext(self._current_mailbox[0]) - 1
        found = []
        batch_start = starting_uid
        while batch_start <= ending_uid:
            batch_end = min(batch_start + self.uid_batch_size - 1, ending_uid)
            data = self.call("UID", "SEARCH", f"UID {batch_start}:{batch_end}")
            found += [int(uid_bytes) for uid_bytes in data[0].split()]
            batch_start = batch_end + 1
        return [uid for uid in found if uid >= starting_uid]

    def uidvalidity(self, mailbox):
        string = self.call("STATUS", mailbox, "(UIDVALIDITY)")[0]
        return int(re.search(br"UIDVALIDITY (\d+)", string).group(1))

    def uidnext(self, mailbox):
        string = self.call("STATUS", mailbox, "(UIDNEXT)")[0]
        return int(re.search(br"UIDNEXT (\d+)", string).group(1))

    def login(self):
        return self.call("LOGIN", self.config.imap["username"], self.config.imap["password"])

    def logout(self):
        return self.conn.logout()

    @cached_property
    def mailboxes(self):
        pattern = re.compile(br"\((?P<flags>.*?)\) \"(?P<delimiter>.*)\" (?P<name>.*)")
        return [pattern.match(mbox).groupdict() for mbox in self.call("list")]
