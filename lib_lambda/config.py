import os
from dataclasses import dataclass, field

MiB = 1024 * 1024

DEFAULT_BUCKET = "my-mail-archive"

# Prefixes at the top of the bucket that are not "<email address>/" roots.
IGNORED_TOP_LEVEL_PREFIXES = ("v1/",)


def _env_int(name, default):
    value = os.environ.get(name)
    return default if value in (None, "") else int(value)


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name):
    value = os.environ.get(name, "")
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


@dataclass
class ArchiveConfig:
    """All knobs for the archiver, sourced from the environment by default.

    Any field can be overridden per-invocation via the Lambda ``event`` payload
    (see ``ArchiveConfig.from_event``).
    """

    bucket: str = DEFAULT_BUCKET

    # Roll up once this many bytes of hot objects have accumulated.
    target_bytes: int = 250 * MiB
    # Never build a tar smaller than this (unless ``force`` is set).
    min_bytes: int = 250 * MiB
    # Hard stop for a single tar so a huge backlog is split into several files.
    max_bytes: int = 4096 * MiB
    max_objects_per_archive: int = 200_000

    # Multipart part size. RAM usage is roughly one part plus the prefetch queue.
    part_size: int = 16 * MiB
    # Number of source objects fetched concurrently ahead of the tar writer.
    prefetch: int = 8

    # Only ``*.eml.zst`` objects get bundled; metadata JSON stays hot/queryable.
    source_suffix: str = ".eml.zst"
    # Sub-path (under "<address>/<mailbox>/") holding the hot objects.
    source_subprefix: str = "email/"
    # Where the tar bundles land, and their storage class.
    archive_subprefix: str = "archive/"
    storage_class: str = "DEEP_ARCHIVE"
    # Manifests stay in STANDARD so bundle contents are searchable without a restore.
    manifest_storage_class: str = "STANDARD"
    write_manifest: bool = True

    # Skip objects younger than this, so we never race the uploader.
    min_age_seconds: int = 3600

    # Explicit list of "<address>/<mailbox>/" roots. Empty means auto-discover.
    prefixes: list[str] = field(default_factory=list)
    # Only archive these addresses when auto-discovering (empty means all).
    addresses: list[str] = field(default_factory=list)

    max_archives_per_run: int = 4
    # Stop starting/continuing work when less than this much Lambda time is left.
    time_reserve_ms: int = 90_000

    delete_sources: bool = True
    dry_run: bool = False
    # Build an under-sized tar anyway (useful for a manual final flush).
    force: bool = False

    @classmethod
    def from_env(cls):
        return cls(
            bucket=os.environ.get("ARCHIVE_BUCKET", DEFAULT_BUCKET),
            target_bytes=_env_int("ARCHIVE_TARGET_BYTES", 250 * MiB),
            min_bytes=_env_int("ARCHIVE_MIN_BYTES", _env_int("ARCHIVE_TARGET_BYTES", 250 * MiB)),
            max_bytes=_env_int("ARCHIVE_MAX_BYTES", 4096 * MiB),
            max_objects_per_archive=_env_int("ARCHIVE_MAX_OBJECTS", 200_000),
            part_size=_env_int("ARCHIVE_PART_SIZE", 16 * MiB),
            prefetch=_env_int("ARCHIVE_PREFETCH", 8),
            source_suffix=os.environ.get("ARCHIVE_SOURCE_SUFFIX", ".eml.zst"),
            source_subprefix=os.environ.get("ARCHIVE_SOURCE_SUBPREFIX", "email/"),
            archive_subprefix=os.environ.get("ARCHIVE_SUBPREFIX", "archive/"),
            storage_class=os.environ.get("ARCHIVE_STORAGE_CLASS", "DEEP_ARCHIVE"),
            manifest_storage_class=os.environ.get("ARCHIVE_MANIFEST_STORAGE_CLASS", "STANDARD"),
            write_manifest=_env_bool("ARCHIVE_WRITE_MANIFEST", True),
            min_age_seconds=_env_int("ARCHIVE_MIN_AGE_SECONDS", 3600),
            prefixes=_env_list("ARCHIVE_PREFIXES"),
            addresses=_env_list("ARCHIVE_ADDRESSES"),
            max_archives_per_run=_env_int("ARCHIVE_MAX_ARCHIVES_PER_RUN", 4),
            time_reserve_ms=_env_int("ARCHIVE_TIME_RESERVE_MS", 90_000),
            delete_sources=_env_bool("ARCHIVE_DELETE_SOURCES", True),
            dry_run=_env_bool("ARCHIVE_DRY_RUN", False),
            force=_env_bool("ARCHIVE_FORCE", False),
        )

    @classmethod
    def from_event(cls, event=None):
        config = cls.from_env()
        for key, value in (event or {}).items():
            if hasattr(config, key):
                setattr(config, key, value)
        config.validate()
        return config

    def validate(self):
        if self.part_size < 5 * MiB:
            raise ValueError(f"part_size must be >= 5 MiB (S3 multipart minimum), got {self.part_size}")
        if self.max_bytes < self.min_bytes:
            raise ValueError("max_bytes must be >= min_bytes")
        if not self.bucket:
            raise ValueError("bucket is required")
        return self

    def state_key(self, prefix_root: str) -> str:
        return f"{prefix_root}archive-state.json"

    def source_prefix(self, prefix_root: str) -> str:
        return f"{prefix_root}{self.source_subprefix}"

    def archive_key(self, prefix_root: str, number: int) -> str:
        return f"{prefix_root}{self.archive_subprefix}archive-{number:06d}.tar"

    def manifest_key(self, prefix_root: str, number: int) -> str:
        return f"{prefix_root}{self.archive_subprefix}archive-{number:06d}.manifest.jsonl.zst"
