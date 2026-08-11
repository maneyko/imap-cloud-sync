import json
from datetime import datetime, timezone

SCHEMA_VERSION = 1


class ArchiveState:
    """Per-mailbox archiver checkpoint, stored as JSON in S3.

    {
      "schema_version": 1,
      "source_prefix": "me@example.com/INBOX/email/",
      "last_archived_key": "me@example.com/INBOX/email/2026/06/30/23-59-59.1782863999.uid-987654.eml.zst",
      "next_archive_number": 42,
      "archive_count": 41,
      "archived_objects": 51234,
      "archived_bytes": 10737418240,
      "pending_objects": 1243,
      "pending_bytes": 48392012,
      "updated_at": "2026-08-11T17:29:00+00:00"
    }

    Writes use S3 conditional puts (``If-Match`` on the ETag) so two overlapping
    Lambda invocations cannot silently clobber each other's checkpoint.
    """

    DEFAULTS = {
        "schema_version": SCHEMA_VERSION,
        "source_prefix": None,
        "last_archived_key": None,
        "next_archive_number": 1,
        "archive_count": 0,
        "archived_objects": 0,
        "archived_bytes": 0,
        "pending_objects": 0,
        "pending_bytes": 0,
        "updated_at": None,
    }

    def __init__(self, s3, key: str, source_prefix: str):
        self.s3 = s3
        self.key = key
        self._etag = None
        self._state = dict(self.DEFAULTS)
        self._state["source_prefix"] = source_prefix
        self.load()

    def load(self):
        remote, etag = self.s3.get_json(self.key)
        if remote:
            if remote.get("schema_version") != SCHEMA_VERSION:
                raise RuntimeError(f"Unsupported archive state schema in s3://{self.s3.bucket}/{self.key}: {remote}")
            self._state = dict(self.DEFAULTS) | remote
            self._etag = etag
        return self

    def save(self):
        self._state["updated_at"] = datetime.now(timezone.utc).isoformat()
        body = json.dumps(self._state, indent=2, sort_keys=True).encode()
        kwargs = {"ContentType": "application/json"}
        if self._etag:
            kwargs["etag_match"] = self._etag
        else:
            # Create-only: fails if somebody else created the state meanwhile.
            kwargs["IfNoneMatch"] = "*"
        response = self.s3.put(self.key, body, **kwargs)
        self._etag = response["ETag"]
        return self

    def record_archive(self, *, last_key: str, objects: int, size_bytes: int):
        self._state["last_archived_key"] = last_key
        self._state["next_archive_number"] = self.next_archive_number + 1
        self._state["archive_count"] = self._state["archive_count"] + 1
        self._state["archived_objects"] = self._state["archived_objects"] + objects
        self._state["archived_bytes"] = self._state["archived_bytes"] + size_bytes
        self._state["pending_objects"] = 0
        self._state["pending_bytes"] = 0
        return self

    def record_pending(self, objects: int, size_bytes: int):
        self._state["pending_objects"] = objects
        self._state["pending_bytes"] = size_bytes
        return self

    @property
    def last_archived_key(self):
        return self._state["last_archived_key"]

    @property
    def next_archive_number(self) -> int:
        return self._state["next_archive_number"]

    def as_dict(self):
        return dict(self._state)
