import os
import signal
import subprocess
import sys

from lib import config

class NoSuchKey(Exception): pass

_interrupt_signal = None

def interrupted():
    """The signal we have been asked to shut down with, or None."""
    return _interrupt_signal

def install_interrupt_handlers():
    """Turn SIGINT/SIGTERM into a request to stop at the next checkpoint.

    A second signal raises KeyboardInterrupt from the handler, aborting wherever
    we happen to be, so an unresponsive run can always be killed with Ctrl-C.
    """
    def handle(signum, _frame):
        global _interrupt_signal
        if _interrupt_signal is not None:
            raise KeyboardInterrupt(f"{signal.Signals(signum).name} received twice")
        _interrupt_signal = signal.Signals(signum)
        print(f"\nReceived {_interrupt_signal.name}")

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handle)

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
    import platform
    aws_opts = {}
    if platform.uname().system == "Darwin":
        aws_opts["profile_name"] = "personal"
    s3_client = boto3.Session(**aws_opts).client("s3")
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

def save_to_s3(bucket_name, path, data, **kwargs):
    path = str(path).removeprefix("/")
    if s3_client:
        kwargs = {"".join(w.capitalize() for w in k.split("_")): v for k, v in kwargs.items()}
        return s3_client.put_object(Bucket=bucket_name, Key=path, Body=data, **kwargs)
    else:
        cmd = ["aws", "s3", "cp", "-", f"s3://{bucket_name}/{path}"]
        for k, v in kwargs.items():
            k = k.replace("_", "-")
            cmd.append(f"--{k}")
            cmd.append(v)
        return subprocess.run(cmd, input=data)

def read_from_s3(bucket_name, path) -> bytes:
    path = str(path).removeprefix("/")
    if s3_client:
        try:
            return s3_client.get_object(Bucket=bucket_name, Key=path)["Body"].read()
        except Exception as err:
            # AccessDenied occurs if IAM role doesn't have s3:ListBucket.
            if err.response["Error"]["Code"] in ("NoSuchKey", "AccessDenied"):
                raise NoSuchKey(*err.args)
            else:
                raise err
    else:
        result = subprocess.run(
            ["aws", "s3", "cp", f"s3://{bucket_name}/{path}", "-"],
            capture_output=True
        )
        if b'Not Found' in result.stderr:
            raise NoSuchKey(result.stderr.decode())
        return result.stdout
