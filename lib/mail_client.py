from functools import cached_property
import imaplib
import re

from lib.config import Config

class MailClient:
    def __init__(self, email_address: str):
        self.email_address = email_address
        self.config = Config(self.email_address)
        self.create_connection()
        resp = self.login()[0].decode()
        print(f"Logged in to IMAP server {self.conn.host} as user {self.config.imap["username"]}: {resp}")

    def create_connection(self):
        self.conn = imaplib.IMAP4_SSL(self.config.imap["server"], self.config.imap["port"])

    def call(self, func, *args, **kwargs):
        try:
            typ, data = getattr(self.conn, func.lower())(*args, **kwargs)
        except imaplib.IMAP4.abort:
            self.conn.close()
            self.create_connection()
            typ, data = getattr(self.conn, func.lower())(*args, **kwargs)
        if typ != "OK":
            raise RuntimeError(f"IMAP client returned unsuccessful response: typ={typ}, data={data}")
        return data

    def select(self, mailbox, **kwargs):
        kwargs = {"readonly": True} | kwargs
        return self.call("SELECT", mailbox, **kwargs)

    def uids(self, starting_uid, ending_uid=None) -> list[bytes]:
        "Return list of sorted UIDs from the specified bounds."
        ending_uid = ending_uid or "*"
        data = self.call("UID", "SEARCH", f"UID {starting_uid}:{ending_uid}")
        return data[0].split()

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
