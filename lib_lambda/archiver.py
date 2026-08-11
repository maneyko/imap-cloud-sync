import time

from lib_lambda.config import IGNORED_TOP_LEVEL_PREFIXES
from lib_lambda.s3util import S3, human_bytes
from lib_lambda.state import ArchiveState
from lib_lambda.tar_builder import TarBundleBuilder


class Deadline:
    """Wraps the Lambda context so the run stops well before it is killed."""

    def __init__(self, context=None, reserve_ms: int = 90_000):
        self.context = context
        self.reserve_ms = reserve_ms
        self.started = time.monotonic()

    def remaining_ms(self) -> float:
        if self.context is not None and hasattr(self.context, "get_remaining_time_in_millis"):
            return self.context.get_remaining_time_in_millis()
        return float("inf")

    def ok(self) -> bool:
        return self.remaining_ms() > self.reserve_ms

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started


class PrefixArchiver:
    """Archives one "<address>/<mailbox>/" root."""

    def __init__(self, s3: S3, config, prefix_root: str, deadline: Deadline | None = None):
        self.s3 = s3
        self.config = config
        self.prefix_root = prefix_root
        self.source_prefix = config.source_prefix(prefix_root)
        self.deadline = deadline or Deadline()
        self.state = ArchiveState(s3, config.state_key(prefix_root), self.source_prefix)
        self.builder = TarBundleBuilder(s3, config, source_prefix=self.source_prefix)

    # -- step 1 & 2: list objects after the checkpoint, accumulate ---------

    def select_objects(self) -> tuple[list[dict], bool]:
        """Return (objects, hit_target) for the next bundle.

        Objects are taken in key order (which is chronological given the
        ``%Y/%m/%d/%H-%M-%S`` path template) starting right after
        ``last_archived_key``, until the target size is reached.
        """
        cutoff = time.time() - self.config.min_age_seconds
        objects: list[dict] = []
        pending_bytes = 0
        hit_target = False

        for obj in self.s3.list_objects(self.source_prefix, start_after=self.state.last_archived_key):
            key = obj["Key"]
            if not key.endswith(self.config.source_suffix):
                continue
            if obj["LastModified"].timestamp() > cutoff:
                # Too fresh: the uploader may still be working near this key.
                # Stop here rather than skipping, so the checkpoint stays contiguous.
                break
            if obj["Size"] == 0:
                print(f"WARNING: skipping empty object {key}")
                continue

            objects.append(obj)
            pending_bytes += obj["Size"]

            if pending_bytes >= self.config.target_bytes:
                hit_target = True
            if pending_bytes >= self.config.max_bytes or len(objects) >= self.config.max_objects_per_archive:
                break
            if hit_target:
                break

        return objects, pending_bytes >= self.config.min_bytes

    # -- step 3: build, checkpoint, delete --------------------------------

    def run_once(self) -> dict | None:
        """Build at most one archive. Returns a summary, or None if nothing to do."""
        objects, enough = self.select_objects()
        pending_bytes = sum(obj["Size"] for obj in objects)

        if not objects:
            return None

        if not enough and not self.config.force:
            print(
                f"{self.prefix_root}: {len(objects):,} objects / {human_bytes(pending_bytes)} pending "
                f"(< {human_bytes(self.config.min_bytes)}), waiting"
            )
            if not self.config.dry_run:
                self.state.record_pending(len(objects), pending_bytes).save()
            return None

        number = self.state.next_archive_number
        tar_key = self.config.archive_key(self.prefix_root, number)

        if self.config.dry_run:
            print(f"DRY RUN: would bundle {len(objects):,} objects / {human_bytes(pending_bytes)} into {tar_key}")
            return {
                "prefix": self.prefix_root,
                "tar_key": tar_key,
                "objects": len(objects),
                "source_bytes": pending_bytes,
                "dry_run": True,
            }

        print(f"{self.prefix_root}: bundling {len(objects):,} objects / {human_bytes(pending_bytes)} -> {tar_key}")
        result = self.builder.build(objects, tar_key, should_continue=self.deadline.ok)

        if not result["members"]:
            # Ran out of time before writing anything; the (empty) tar is harmless
            # but we do not advance the checkpoint.
            print(f"{self.prefix_root}: no members written, leaving checkpoint untouched")
            return None

        # Checkpoint BEFORE deleting: if the delete fails we only leak objects
        # that are already safely inside the tar, never lose data.
        self.state.record_archive(
            last_key=result["members"][-1]["key"],
            objects=len(result["members"]),
            size_bytes=result["source_bytes"],
        ).save()

        deleted, errors = self.delete_sources(result["keys"])

        print(
            f"{self.prefix_root}: wrote {tar_key} "
            f"({human_bytes(result['tar_bytes'])}, {result['parts']} parts, {len(result['members']):,} members), "
            f"deleted {deleted:,} source objects"
        )

        return {
            "prefix": self.prefix_root,
            "tar_key": tar_key,
            "manifest_key": result.get("manifest_key"),
            "storage_class": self.config.storage_class,
            "objects": len(result["members"]),
            "source_bytes": result["source_bytes"],
            "tar_bytes": result["tar_bytes"],
            "deleted": deleted,
            "delete_errors": errors,
            "last_archived_key": self.state.last_archived_key,
        }

    def delete_sources(self, keys: list[str]) -> tuple[int, list[dict]]:
        if not self.config.delete_sources:
            print(f"{self.prefix_root}: delete_sources disabled, keeping {len(keys):,} source objects")
            return 0, []
        errors = self.s3.delete_keys(keys)
        for error in errors[:10]:
            print(f"ERROR: failed to delete {error.get('Key')}: {error.get('Code')} {error.get('Message')}")
        return len(keys) - len(errors), errors

    def run(self) -> list[dict]:
        """Build archives for this prefix until it is drained or limits are hit."""
        results = []
        while len(results) < self.config.max_archives_per_run and self.deadline.ok():
            result = self.run_once()
            if result is None:
                break
            results.append(result)
            if self.config.dry_run:
                # State never advances in a dry run, so a second pass would
                # just report the same objects again.
                break
        return results


class Archiver:
    """Discovers mailbox roots and archives each of them."""

    def __init__(self, config, context=None, s3: S3 | None = None):
        self.config = config
        self.s3 = s3 or S3(config.bucket)
        self.deadline = Deadline(context, config.time_reserve_ms)

    def discover_prefixes(self) -> list[str]:
        """Find every "<address>/<mailbox>/" root that has hot objects."""
        if self.config.prefixes:
            return [prefix if prefix.endswith("/") else prefix + "/" for prefix in self.config.prefixes]

        roots = []
        for address_prefix in self.s3.list_common_prefixes():
            if address_prefix in IGNORED_TOP_LEVEL_PREFIXES or "@" not in address_prefix:
                continue
            address = address_prefix.rstrip("/")
            if self.config.addresses and address not in self.config.addresses:
                continue
            roots.extend(self.s3.list_common_prefixes(address_prefix))
        return roots

    def run(self) -> dict:
        summary = {
            "bucket": self.config.bucket,
            "archives": [],
            "prefixes": [],
            "objects": 0,
            "source_bytes": 0,
            "tar_bytes": 0,
        }

        for prefix_root in self.discover_prefixes():
            if not self.deadline.ok():
                print(f"Stopping early with {self.deadline.remaining_ms() / 1000:.0f}s left before timeout")
                summary["stopped_early"] = True
                break

            summary["prefixes"].append(prefix_root)
            archiver = PrefixArchiver(self.s3, self.config, prefix_root, self.deadline)
            for result in archiver.run():
                summary["archives"].append(result)
                summary["objects"] += result["objects"]
                summary["source_bytes"] += result["source_bytes"]
                summary["tar_bytes"] += result.get("tar_bytes", 0)

        summary["archive_count"] = len(summary["archives"])
        summary["elapsed_s"] = round(self.deadline.elapsed_s(), 1)
        return summary
