import os
import subprocess
import sys

from lib import config

zstd = None
if sys.version_info >= (3, 14):
    from compression import zstd
else:
    try:
        subprocess.run(["zstd", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("""zstd CLI must be installed when using Python < 3.14
apt install -y zstd
brew install zstd
""")
        sys.exit(1)

s3_client = None
try:
    import boto3
    s3_client = boto3.client("s3")
except ModuleNotFoundError:
    try:
        subprocess.run(["aws", "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        print("""AWS CLI must be installed if boto3 is not available.
apt install -y awscli
brew install awscli
""")
        sys.exit(1)


def zstd_compress(data: bytes, level: int = 9) -> bytes:
    if zstd:
        return zstd.compress(data, level=level)
    else:
        return subprocess.run(["zstd", f"-{level}"], input=data, capture_output=True)


def save_to_s3(bucket_name, path, data):
    path = str(path).removeprefix("/")
    if s3_client:
        return s3_client.put_object(Bucket=bucket_name, Key=path, Body=data)
    else:
        return subprocess.run(
            ["aws", "s3", "cp", "-", f"s3://{bucket_name}/{path}"],
            input=data,
        )

def read_from_s3(bucket_name, path) -> bytes:
    path = str(path).removeprefix("/")
    if s3_client:
        return s3_client.get_object(Bucket=bucket_name, Key=path)["Body"].read()
    else:
        return subprocess.run(
            ["aws", "s3", "cp", f"s3://{bucket_name}/{path}", "-"],
            capture_output=True
        ).stdout

