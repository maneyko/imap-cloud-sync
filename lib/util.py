from compression import zstd
import os
import platform
import signal
import subprocess
import sys

import boto3

from lib import config

class NoSuchKey(Exception): pass

aws_opts = {}
if platform.uname().system == "Darwin":
    aws_opts["profile_name"] = "personal"
s3_client = boto3.Session(**aws_opts).client("s3")

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

def zstd_compress(data: bytes, level: int = 9) -> bytes:
    return zstd.compress(data, level=level)

def save_to_s3(bucket_name, path, data, **kwargs):
    path = str(path).removeprefix("/")
    kwargs = {"".join(w.capitalize() for w in k.split("_")): v for k, v in kwargs.items()}
    return s3_client.put_object(Bucket=bucket_name, Key=path, Body=data, **kwargs)

def read_from_s3(bucket_name, path) -> bytes:
    path = str(path).removeprefix("/")
    try:
        return s3_client.get_object(Bucket=bucket_name, Key=path)["Body"].read()
    except Exception as err:
        # AccessDenied occurs if IAM role doesn't have s3:ListBucket.
        if err.response["Error"]["Code"] in ("NoSuchKey", "AccessDenied"):
            raise NoSuchKey(*err.args)
        else:
            raise err
