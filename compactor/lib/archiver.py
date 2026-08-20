import time

from lib.config import IGNORED_TOP_LEVEL_PREFIXES
from lib.s3util import S3, human_bytes
from lib.state import ArchiveState
from lib.tar_builder import TarBundleBuilder


class Deadline:
    """Wraps the Lambda context so the run stops well before it is killed."""

    def __init__(self, context=None, reserve_ms: int = 90_000):
        self.context = context
        self.reserve_ms = reserve_ms
        self.started = time.monotonic()

    def ok(self) -> bool:
        return self.remaining_ms() > self.reserve_ms

    def remaining_ms(self) -> float:
        if hasattr(self.context, "get_remaining_time_in_millis"):
            return self.context.get_remaining_time_in_millis()
        return float("inf")

    def elapsed_s(self) -> float:
        return time.monotonic() - self.started


class MailboxArchiver:
    """Archives one "<address>/<mailbox>/" root."""

    def __init__(self, s3: S3, config, mailbox_prefix: str, deadline: Deadline):
        self.s3 = s3
        self.config = config
        self.mailbox_prefix = mailbox_prefix
        self.source_prefix = config.source_prefix(mailbox_prefix)
        self.deadline = deadline
        self.state = ArchiveState(s3, config.state_key(mailbox_prefix), self.source_prefix)
        self.builder = TarBundleBuilder(s3, config, source_prefix=self.source_prefix)

    def run(self) -> list[dict]:
        """Build archives for this prefix until it is drained or limits are hit."""
        results = []
        while self.deadline.ok():
            result = self.run_once()
            if result is None:
                break
            results.append(result)
        return results

    def run_once(self) -> dict | None:
        """Build at most one archive. Returns a summary, or None if nothing to do."""
        objects = self.select_objects()
        pending_bytes = sum(obj["Size"] for obj in objects)
        if pending_bytes == 0: return

        if not pending_bytes < self.config.min_bytes and not self.config.force:
            print(
                f"{self.mailbox_prefix}: {len(objects):,} objects / {human_bytes(pending_bytes)} pending "
                f"(< {human_bytes(self.config.min_bytes)}), waiting"
            )
            self.state.record_pending(len(objects), pending_bytes).save()
            return None

        tar_key = self.config.archive_key(self.mailbox_prefix, self.state.next_archive_number)

        print(f"{self.mailbox_prefix}: bundling {len(objects):,} objects / {human_bytes(pending_bytes)} -> {tar_key}")
        result = self.builder.build(objects, tar_key, should_continue=self.deadline.ok)

        if not result["members"]:
            print(f"{self.mailbox_prefix}: no members written, ran out of time, leaving checkpoint untouched")
            return None

        self.state.record_archive(
            last_key=result["members"][-1]["key"],
            objects=len(result["members"]),
            size_bytes=result["source_bytes"],
        ).save()

        deleted, errors = self.delete_sources(result["keys"])

        print(
            f"{self.mailbox_prefix}: wrote {tar_key} "
            f"({human_bytes(result['tar_bytes'])}, {result['parts']} parts, {len(result['members']):,} members), "
            f"deleted {deleted:,} source objects"
        )

        return {
            "prefix": self.mailbox_prefix,
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

    def select_objects(self) -> list[dict]:
        """Return (objects, hit_target) for the next bundle."""
        cutoff = time.time() - self.config.min_age_seconds
        objects: list[dict] = []
        pending_bytes = 0

        for obj in self.s3.list_objects(self.source_prefix, start_after=self.state.last_archived_key):
            key = obj["Key"]
            if not key.endswith(self.config.source_suffix):
                continue
            if obj["LastModified"].timestamp() > cutoff:
                break

            objects.append(obj)
            pending_bytes += obj["Size"]

            if pending_bytes >= self.config.min_bytes or len(objects) >= self.config.max_objects_per_archive:
                break

        return objects

    def delete_sources(self, keys: list[str]) -> tuple[int, list[dict]]:
        errors = self.s3.delete_keys(keys)
        for error in errors[:10]:
            print(f"ERROR: failed to delete {error.get('Key')}: {error.get('Code')} {error.get('Message')}")
        return len(keys) - len(errors), errors


class Archiver:
    """Discovers mailbox roots and archives each of them."""

    def __init__(self, config, context=None):
        self.config = config
        self.s3 = S3(config.bucket)
        self.deadline = Deadline(context, config.time_reserve_ms)

    def run(self) -> dict:
        summary = {
            "bucket": self.config.bucket,
            "archives": [],
            "prefixes": [],
            "objects": 0,
            "source_bytes": 0,
            "tar_bytes": 0,
        }

        for mailbox_prefix in self.discover_mailboxes():
            if not self.deadline.ok():
                print(f"Stopping early with {self.deadline.remaining_ms() / 1000:.0f}s left before timeout")
                summary["stopped_early"] = True
                break

            summary["prefixes"].append(mailbox_prefix)
            archiver = MailboxArchiver(self.s3, self.config, mailbox_prefix, self.deadline)
            for result in archiver.run():
                summary["archives"].append(result)
                summary["objects"] += result["objects"]
                summary["source_bytes"] += result["source_bytes"]
                summary["tar_bytes"] += result.get("tar_bytes", 0)

        summary["archive_count"] = len(summary["archives"])
        summary["elapsed_s"] = round(self.deadline.elapsed_s(), 1)
        return summary

    def discover_mailboxes(self) -> list[str]:
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
