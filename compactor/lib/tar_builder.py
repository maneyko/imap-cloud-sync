import io
import json
import tarfile
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from compression import zstd  # Python 3.14 stdlib

from lib.upload import MultipartUploadStream


class TarBundleBuilder:
    """Streams a set of S3 objects into a single ``.tar`` object in S3.

    Source objects are fetched by a small thread pool that runs a bounded
    number of GETs ahead of the tar writer, so the Lambda's memory footprint is
    ``part_size + prefetch * avg_object_size`` (a few tens of MiB), never the
    size of the finished archive.

    The ``.eml.zst`` payloads are stored in the tar uncompressed (they are
    already zstd-compressed) which keeps individual members extractable with
    a ranged read of the tar once it has been restored.
    """

    def __init__(self, s3, config, *, source_prefix: str):
        self.s3 = s3
        self.config = config
        self.source_prefix = source_prefix

    def member_name(self, key: str) -> str:
        """Key relative to the source prefix, e.g. 2026/08/06/21-14-20.…eml.zst."""
        return key.removeprefix(self.source_prefix)

    def build(self, objects: list[dict], tar_key: str, *, should_continue=None) -> dict:
        """Write ``objects`` into ``tar_key``.

        ``should_continue`` is an optional callable returning False when we are
        running out of Lambda time; the tar is then closed early and only the
        members written so far are reported (and therefore only those get
        deleted afterwards).
        """
        members: list[dict] = []
        stream = MultipartUploadStream(
            self.s3,
            tar_key,
            part_size=self.config.part_size,
            storage_class=self.config.storage_class,
            extra_args={"ContentType": "application/x-tar"},
        )
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fetches = deque()
                pending = iter(objects)

                def top_up():
                    while len(fetches) < 1:
                        obj = next(pending, None)
                        if obj is None:
                            break
                        fetches.append((obj, pool.submit(self.s3.get_body, obj["Key"])))

                # mode="w|" is the streaming (non-seekable) tar writer.
                with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                    top_up()
                    while fetches:
                        if should_continue is not None and not should_continue():
                            print(f"Time budget reached: closing {tar_key} after {len(members)} members")
                            break
                        obj, future = fetches.popleft()
                        data = future.result()
                        members.append(self._add(tar, obj, data))
                        top_up()

                # Drain anything still in flight so the pool shuts down cleanly.
                for _, future in fetches:
                    future.cancel()

            response = stream.complete()
        except Exception:
            stream.abort()
            raise

        result = {
            "tar_key": tar_key,
            "tar_bytes": stream.bytes_written,
            "parts": len(stream.parts) or 1,
            "etag": response.get("ETag"),
            "members": members,
            "source_bytes": sum(m["size"] for m in members),
            "keys": [m["key"] for m in members],
        }
        if members and self.config.write_manifest:
            result["manifest_key"] = self.write_manifest(tar_key, result)
        return result

    def _add(self, tar: tarfile.TarFile, obj: dict, data: bytes) -> dict:
        info = tarfile.TarInfo(name=self.member_name(obj["Key"]))
        info.size = len(data)
        info.mtime = int(obj["LastModified"].timestamp())
        info.mode = 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        tar.addfile(info, io.BytesIO(data))
        if len(data) != obj["Size"]:
            print(f"WARNING: size drift for {obj['Key']}: listed={obj['Size']} fetched={len(data)}")
        return {
            "key": obj["Key"],
            "name": info.name,
            "size": len(data),
            "etag": obj.get("ETag", "").strip('"'),
            "last_modified": obj["LastModified"].isoformat(),
        }

    def write_manifest(self, tar_key: str, result: dict) -> str:
        """Store a JSONL listing of the bundle contents next to the tar.

        The manifest stays in STANDARD so "which bundle holds this email?" can
        be answered without a Glacier restore.
        """
        manifest_key = tar_key.removesuffix(".tar") + ".manifest.jsonl.zst"
        lines = [json.dumps({
            "schema_version": 1,
            "type": "header",
            "tar_key": tar_key,
            "source_prefix": self.source_prefix,
            "storage_class": self.config.storage_class,
            "object_count": len(result["members"]),
            "source_bytes": result["source_bytes"],
            "tar_bytes": result["tar_bytes"],
        })]
        lines += [json.dumps(member) for member in result["members"]]
        body = zstd.compress("\n".join(lines).encode() + b"\n", level=9)
        self.s3.put(
            manifest_key,
            body,
            ContentType="application/jsonl+zstd",
            StorageClass=self.config.manifest_storage_class,
        )
        return manifest_key
