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
        "storage_class": "DEEP_ARCHIVE",
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
        from lib.stores import S3Store as Store
        return Store(self.storage["bucket_name"])

def s3_glacier_min_size() -> int:
    # https://aws.amazon.com/s3/pricing/
    # Prices based on us-east-1 (Standard: $0.023 / GB, Deep Archive: $0.00099 / GB)
    GB_in_kb = 1024 * 1024

    P_std = 0.023 / GB_in_kb # Price of Standard per KB
    P_da = 0.00099 / GB_in_kb # Price of Deep Archive per KB

    # Deep Archive monthly cost for file size S (in KB):
    # Cost_DA = (S + 32) * P_da + 8 * P_std
    # Standard monthly cost for file size S (in KB):
    # Cost_Std = S * P_std

    # Break-even when Cost_DA = Cost_Std:
    # S * P_std = (S + 32) * P_da + 8 * P_std
    # S * P_std - S * P_da = 32 * P_da + 8 * P_std
    # S * (P_std - P_da) = 32 * P_da + 8 * P_std
    # S = (32 * P_da + 8 * P_std) / (P_std - P_da)

    # Meaning a file that is 10,034 bytes costs the same to store in STANDARD as DEEP_ARCHIVE.
    return int((32 * P_da + 8 * P_std) / (P_std - P_da) * 1024) + 1

Config.s3_glacier_min_size = s3_glacier_min_size()
