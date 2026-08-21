import re
import time

from lib.config import (
    ARCHIVE_SUBPREFIX,
    BUCKET,
    MIN_AGE_SECONDS,
    MIN_ARCHIVE_BYTES,
    SOURCE_SUBPREFIX,
    SOURCE_SUFFIX,
    TIME_RESERVE_MS,
)
from lib.s3util import S3, human_bytes
from lib.tar_builder import build_tar


def run(context=None) -> dict:
    """Bundle every mailbox that has enough pending email into DEEP_ARCHIVE tars.

    There is no checkpoint file: sources are deleted once they are inside a
    tar, so whatever is left under "<address>/<mailbox>/email/" is exactly what
    still needs archiving.
    """
    started = time.monotonic()
    s3 = S3(BUCKET)
    archives = []

    for mailbox_prefix in discover_mailboxes(s3):
        while time_left_ms(context) > TIME_RESERVE_MS:
            result = archive_once(s3, mailbox_prefix)
            if result is None:
                break
            archives.append(result)

    return {
        "bucket": BUCKET,
        "archives": archives,
        "objects": sum(archive["objects"] for archive in archives),
        "source_bytes": sum(archive["source_bytes"] for archive in archives),
        "tar_bytes": sum(archive["tar_bytes"] for archive in archives),
        "elapsed_s": round(time.monotonic() - started, 1),
    }


def archive_once(s3: S3, mailbox_prefix: str) -> dict | None:
    """Build one tar for this mailbox, or return None if there is not enough email."""
    source_prefix = mailbox_prefix + SOURCE_SUBPREFIX
    objects = select_objects(s3, source_prefix)
    pending_bytes = sum(obj["Size"] for obj in objects)

    if pending_bytes < MIN_ARCHIVE_BYTES:
        print(
            f"{mailbox_prefix}: {len(objects):,} objects / {human_bytes(pending_bytes)} pending "
            f"(< {human_bytes(MIN_ARCHIVE_BYTES)}), waiting"
        )
        return None

    tar_key = f"{mailbox_prefix}{ARCHIVE_SUBPREFIX}archive-{next_archive_number(s3, mailbox_prefix):06d}.tar"
    print(f"{mailbox_prefix}: bundling {len(objects):,} objects / {human_bytes(pending_bytes)} -> {tar_key}")

    result = build_tar(s3, objects, tar_key, source_prefix)
    errors = s3.delete_keys(result["keys"])
    for error in errors[:10]:
        print(f"ERROR: failed to delete {error.get('Key')}: {error.get('Code')} {error.get('Message')}")

    print(
        f"{mailbox_prefix}: wrote {tar_key} "
        f"({human_bytes(result['tar_bytes'])}, {result['parts']} parts, {len(result['members']):,} members), "
        f"deleted {len(result['keys']) - len(errors):,} source objects"
    )

    return {
        "prefix": mailbox_prefix,
        "tar_key": tar_key,
        "manifest_key": result["manifest_key"],
        "objects": len(result["members"]),
        "source_bytes": result["source_bytes"],
        "tar_bytes": result["tar_bytes"],
        "delete_errors": errors,
    }


def select_objects(s3: S3, source_prefix: str) -> list[dict]:
    """The oldest objects worth up to one archive, skipping any the uploader may still be writing."""
    cutoff = time.time() - MIN_AGE_SECONDS
    objects = []
    pending_bytes = 0

    for obj in s3.list_objects(source_prefix):
        if not obj["Key"].endswith(SOURCE_SUFFIX):
            continue
        if obj["LastModified"].timestamp() > cutoff:
            break
        objects.append(obj)
        pending_bytes += obj["Size"]
        if pending_bytes >= MIN_ARCHIVE_BYTES:
            break

    return objects


def next_archive_number(s3: S3, mailbox_prefix: str) -> int:
    numbers = [
        int(match.group(1))
        for obj in s3.list_objects(mailbox_prefix + ARCHIVE_SUBPREFIX)
        if (match := re.search(r"archive-(\d+)\.tar$", obj["Key"]))
    ]
    return max(numbers, default=0) + 1


def discover_mailboxes(s3: S3) -> list[str]:
    """Every "<address>/<mailbox>/" root in the bucket."""
    roots = []
    for address_prefix in s3.list_common_prefixes():
        if "@" in address_prefix:
            roots.extend(s3.list_common_prefixes(address_prefix))
    return roots


def time_left_ms(context) -> float:
    if context is None:
        return float("inf")
    return context.get_remaining_time_in_millis()
