import tomllib
from types import SimpleNamespace

# Everything the compactor owns lives here: the settings for this bucket and
# the archives it produces, mirroring each source prefix underneath.
ARCHIVE_ROOT = "bucket-archive/"

CONFIG_KEY = ARCHIVE_ROOT + "config.toml"


def load_settings(s3) -> SimpleNamespace:
    """Read this bucket's settings from s3://<bucket>/bucket-archive/config.toml."""
    return SimpleNamespace(**tomllib.loads(s3.get_body(CONFIG_KEY).decode()))
