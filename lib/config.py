from functools import cached_property
from pathlib import Path
import platform
import tomllib
from zoneinfo import ZoneInfo

DEFAULTS = {
    "imap": {
        "port": 993,
        "mailboxes": [
            '"INBOX"',
            'INBOX',
            '"[Gmail]/All Mail"',
            '"[Gmail]/Sent Mail"',
            '"Sent Items"',
        ],
    },
    "processing": {"batch_size": 5, "max_download_mib": 1024},
    "storage": {
        "bucket_name": "my-mail-archive",
        "timezone": "America/Chicago",
    }
}

app_root = Path(__file__).resolve().parent.parent

# The checkout's secrets/ is gitignored, so real credentials can sit in a
# working tree.
if platform.uname().system == "Darwin":
    SECRETS_DIR = app_root / "secrets"
else:
    SECRETS_DIR = Path("/etc/imap-cloud-sync/secrets")

class Config:
    path_template = "%Y/%m/%d/%H-%M-%S.{epoch}.uid-{uid}"

    def __init__(self, email_address: str):
        self.email_address = email_address
        with open(SECRETS_DIR / f"{email_address}.toml", "rb") as f:
            self.config = tomllib.load(f)

    @cached_property
    def storage(self):
        self.config["storage"] = DEFAULTS["storage"] | self.config.setdefault("storage", {})
        return self.config["storage"]

    @cached_property
    def imap(self):
        conf = self.config["imap"] = DEFAULTS["imap"] | self.config["imap"]
        mailboxes = conf["mailboxes"]
        if self.email_address.endswith("@gmail.com"):
            # 'All Mail' mailbox contains everything
            conf["mailboxes"] = [mbox for mbox in conf["mailboxes"] if mbox not in ["INBOX", '"INBOX"']]
        return conf

    @cached_property
    def processing(self):
        self.config["processing"] = DEFAULTS["processing"] | self.config.setdefault("processing", {})
        return self.config["processing"]

    @cached_property
    def batch_size(self) -> int:
        return self.processing["batch_size"]

    @cached_property
    def max_download_mib(self) -> int:
        "Ceiling on how much this account pulls from IMAP per run of main.py."
        return self.processing["max_download_mib"]

    @cached_property
    def timezone(self):
        return ZoneInfo(self.storage["timezone"])

    @cached_property
    def store(self):
        from lib.stores import S3Store as Store
        return Store(self.storage["bucket_name"])
