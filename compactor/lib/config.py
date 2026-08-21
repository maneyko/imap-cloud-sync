MiB = 1024 * 1024

BUCKET = "my-mail-archive"

SOURCE_SUFFIX = ".eml.zst"
SOURCE_SUBPREFIX = "email/"
ARCHIVE_SUBPREFIX = "archive/"

STORAGE_CLASS = "DEEP_ARCHIVE"
MANIFEST_STORAGE_CLASS = "STANDARD"

MIN_ARCHIVE_BYTES = 250 * MiB
MIN_AGE_SECONDS = 3600
PART_SIZE = 16 * MiB

# Only start another archive if this much Lambda time is left; one archive
# takes a couple of minutes.
TIME_RESERVE_MS = 300_000
