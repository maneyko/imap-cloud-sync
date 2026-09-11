#!/usr/bin/env -S PYTHONUNBUFFERED=1 uv run --script

# /// script
# dependencies = ["boto3"]
# requires-python = ">=3.14"
# ///

"""The program is lib/__main__.py. This file exists because uv reads the script
header above from the file it is handed, which is the one systemd executes."""

from lib.__main__ import run

if __name__ == "__main__":
    run()
