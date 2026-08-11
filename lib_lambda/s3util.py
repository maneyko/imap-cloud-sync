import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError


def human_bytes(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num) < 1024 or unit == "TiB":
            return f"{num:,.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024


class S3:
    """Thin wrapper over the bits of the S3 API the archiver needs."""

    def __init__(self, bucket: str, client=None):
        self.bucket = bucket
        self.client = client or boto3.client(
            "s3",
            config=BotoConfig(
                retries={"max_attempts": 10, "mode": "standard"},
                max_pool_connections=32,
            ),
        )

    # -- listing ---------------------------------------------------------

    def list_objects(self, prefix: str, start_after: str | None = None):
        """Yield object dicts under ``prefix`` in lexicographic (== chronological) order."""
        kwargs = {"Bucket": self.bucket, "Prefix": prefix}
        if start_after:
            kwargs["StartAfter"] = start_after
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(**kwargs):
            yield from page.get("Contents", [])

    def list_common_prefixes(self, prefix: str = "") -> list[str]:
        """Return the immediate "directories" under ``prefix``."""
        result = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix, Delimiter="/"):
            result.extend(item["Prefix"] for item in page.get("CommonPrefixes", []))
        return result

    # -- objects ---------------------------------------------------------

    def get_body(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def get_json(self, key: str):
        """Return (parsed_json, etag), or (None, None) when the key is absent."""
        import json
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.NoSuchKey:
            return None, None
        except ClientError as err:
            if err.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None, None
            raise
        return json.loads(response["Body"].read()), response["ETag"]

    def head(self, key: str):
        return self.client.head_object(Bucket=self.bucket, Key=key)

    def put(self, key: str, body: bytes, etag_match: str | None = None, **kwargs):
        if etag_match:
            kwargs["IfMatch"] = etag_match
        return self.client.put_object(Bucket=self.bucket, Key=key, Body=body, **kwargs)

    def delete_keys(self, keys: list[str]) -> list[dict]:
        """Delete keys in batches of 1000. Returns the list of errors (if any)."""
        errors = []
        for i in range(0, len(keys), 1000):
            batch = keys[i:i + 1000]
            response = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
            )
            errors.extend(response.get("Errors", []))
        return errors
