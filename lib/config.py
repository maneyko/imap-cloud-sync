from functools import cached_property
from pathlib import Path
import tomllib
from zoneinfo import ZoneInfo

DEFAULTS = {
    "imap": {
        "port": 993,
        "mailboxes": [
            '"INBOX"',
            'INBOX',
            '"[Gmail]/Sent Mail"',
            '"Sent Items"',
        ],
    },
    "processing": {"batch_size": 5},
    "storage": {
        "bucket_name": "my-mail-archive",
        "timezone": "America/Chicago",
    }
}

app_root = Path(__file__).resolve().parent.parent

SECRETS_DIR = app_root / "secrets"

class Config:
    path_template = "%Y/%m/%d/%H-%M-%S.{epoch}.uid-{uid}"

    def __init__(self, email_address: str):
        self.email_address = email_address
        with open(SECRETS_DIR / f"{email_address}.toml", "rb") as f:
            self.config = tomllib.load(f)

    @cached_property
    def storage(self):
        # return DEFAULTS["storage"] | self.config.setdefault("storage", {})
        self.config["storage"] = DEFAULTS["storage"] | self.config.setdefault("storage", {})
        return self.config["storage"]

    @cached_property
    def imap(self):
        # return DEFAULTS["imap"] | self.config["imap"]
        self.config["imap"] = DEFAULTS["imap"] | self.config["imap"]
        return self.config["imap"]

    @cached_property
    def batch_size(self) -> int:
        self.config.setdefault("processing", {})
        self.config["processing"] = DEFAULTS["processing"] | self.config["processing"]
        return self.config["processing"]["batch_size"]

    @cached_property
    def timezone(self):
        return ZoneInfo(self.storage["timezone"])

    @cached_property
    def store(self):
        from lib.stores import FileStore
        return FileStore(self.storage["bucket_name"])
