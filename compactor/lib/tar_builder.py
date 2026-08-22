import io
import json
import tarfile
from compression import zstd  # Python 3.14 stdlib

from lib.s3util import MultipartUploadStream


class TarBuilder:
    """Streams the objects under one source prefix into tar bundles."""

    def __init__(self, s3, source_prefix: str, settings):
        self.s3 = s3
        self.source_prefix = source_prefix
        self.settings = settings

    def build(self, objects: list[dict], tar_key: str) -> dict:
        """Stream ``objects`` into ``tar_key`` and write a manifest beside it."""
        members = []
        stream = MultipartUploadStream(
            self.s3, tar_key,
            part_size=self.settings.part_size_mib*1024**2,
            storage_class=self.settings.archive_storage_class,
            content_type="application/x-tar",
        )
        try:
            # mode="w|" is the streaming (non-seekable) tar writer.
            with tarfile.open(fileobj=stream, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for obj in objects:
                    members.append(self.add_member(tar, obj))
            stream.complete()
        except Exception:
            stream.abort()
            raise

        result = {
            "tar_key": tar_key,
            "tar_bytes": stream.bytes_written,
            "parts": len(stream.parts) or 1,
            "members": members,
            "source_bytes": sum(member["size"] for member in members),
            "keys": [member["key"] for member in members],
        }
        result["manifest_key"] = self.write_manifest(result)
        return result

    def add_member(self, tar: tarfile.TarFile, obj: dict) -> dict:
        data = self.s3.get_body(obj["Key"])
        info = tarfile.TarInfo(name=obj["Key"].removeprefix(self.source_prefix))
        info.size = len(data)
        info.mtime = int(obj["LastModified"].timestamp())
        info.mode = 0o644
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        tar.addfile(info, io.BytesIO(data))
        return {
            "key": obj["Key"],
            "name": info.name,
            "size": len(data),
            "etag": obj.get("ETag", "").strip('"'),
            "last_modified": obj["LastModified"].isoformat(),
        }

    def write_manifest(self, result: dict) -> str:
        """Store a JSONL listing of the bundle contents next to the tar.

        The manifest stays in STANDARD so "which bundle holds this object?" can
        be answered without a Glacier restore.
        """
        manifest_key = result["tar_key"].removesuffix(".tar") + ".manifest.jsonl.zst"
        lines = [json.dumps({
            "type": "header",
            "tar_key": result["tar_key"],
            "source_prefix": self.source_prefix,
            "storage_class": self.settings.archive_storage_class,
            "object_count": len(result["members"]),
            "source_bytes": result["source_bytes"],
            "tar_bytes": result["tar_bytes"],
        })]
        lines += [json.dumps(member) for member in result["members"]]
        self.s3.put(
            manifest_key,
            zstd.compress("\n".join(lines).encode() + b"\n", level=9),
            ContentType="application/jsonl+zstd",
            StorageClass=self.settings.manifest_storage_class,
        )
        return manifest_key
