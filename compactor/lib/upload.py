import io


class MultipartUploadStream(io.RawIOBase):
    """A write-only, file-like object that streams into an S3 multipart upload.

    Bytes are buffered until ``part_size`` is reached, then flushed to S3 as a
    part, so peak memory stays at roughly one part regardless of object size.

    The multipart upload is created lazily: if the whole stream turns out to be
    smaller than one part, a plain ``put_object`` is used instead (a multipart
    upload with a single sub-5MiB part is legal, but this avoids the ceremony
    and the risk of leaving stray uploads behind).

    Usage::

        with MultipartUploadStream(s3, key, part_size=16 * 1024 * 1024) as stream:
            with tarfile.open(fileobj=stream, mode="w|") as tar:
                ...
        stream.bytes_written
    """

    def __init__(self, s3, key: str, *, part_size: int, storage_class: str | None = None, extra_args: dict | None = None):
        self.s3 = s3
        self.client = s3.client
        self.bucket = s3.bucket
        self.key = key
        self.part_size = part_size
        self.extra_args = dict(extra_args or {})
        if storage_class:
            self.extra_args["StorageClass"] = storage_class

        self.upload_id = None
        self.parts = []
        self.bytes_written = 0
        self._buffer = bytearray()
        self._closed = False
        self._aborted = False

    # -- io plumbing -----------------------------------------------------

    def writable(self):
        return True

    def readable(self):
        return False

    def seekable(self):
        return False

    def write(self, data) -> int:
        if self._closed:
            raise ValueError("write() on a closed MultipartUploadStream")
        view = memoryview(data)
        self._buffer += view
        self.bytes_written += len(view)
        while len(self._buffer) >= self.part_size:
            self._upload_part(bytes(self._buffer[:self.part_size]))
            del self._buffer[:self.part_size]
        return len(view)

    # -- s3 plumbing -----------------------------------------------------

    def _ensure_upload(self):
        if self.upload_id is None:
            response = self.client.create_multipart_upload(
                Bucket=self.bucket, Key=self.key, **self.extra_args
            )
            self.upload_id = response["UploadId"]
        return self.upload_id

    def _upload_part(self, chunk: bytes):
        self._ensure_upload()
        part_number = len(self.parts) + 1
        response = self.client.upload_part(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
            PartNumber=part_number,
            Body=chunk,
        )
        self.parts.append({"ETag": response["ETag"], "PartNumber": part_number})

    def complete(self) -> dict:
        """Flush the tail and commit the object. Returns the S3 response."""
        if self._closed:
            raise ValueError("MultipartUploadStream already finished")
        self._closed = True

        tail = bytes(self._buffer)
        self._buffer.clear()

        if self.upload_id is None:
            # Small enough to skip multipart entirely.
            return self.client.put_object(Bucket=self.bucket, Key=self.key, Body=tail, **self.extra_args)

        if tail:
            self._upload_part(tail)

        return self.client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=self.key,
            UploadId=self.upload_id,
            MultipartUpload={"Parts": self.parts},
        )

    def abort(self):
        """Discard any uploaded parts so we are not billed for orphans."""
        self._closed = True
        self._buffer.clear()
        if self.upload_id and not self._aborted:
            self._aborted = True
            try:
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=self.key, UploadId=self.upload_id)
            except Exception as err:  # best-effort; lifecycle rule should also clean these up
                print(f"WARNING: failed to abort multipart upload {self.key}: {err}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.abort()
        return False
