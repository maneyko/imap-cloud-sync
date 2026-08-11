"""Helpers for the S3 "archiver" Lambda.

The archiver rolls up the individual (hot) ``*.eml.zst`` objects written by
``main.py`` into large ``.tar`` bundles stored in DEEP_ARCHIVE, then deletes
the individual objects that were bundled.

Nothing in this package touches IMAP or the ``secrets/`` directory: the Lambda
only ever needs S3 permissions.
"""

from lib_lambda.config import ArchiveConfig
from lib_lambda.state import ArchiveState
from lib_lambda.s3util import S3, human_bytes
from lib_lambda.upload import MultipartUploadStream
from lib_lambda.tar_builder import TarBundleBuilder
from lib_lambda.archiver import Archiver, PrefixArchiver

__all__ = [
    "ArchiveConfig",
    "ArchiveState",
    "S3",
    "MultipartUploadStream",
    "TarBundleBuilder",
    "Archiver",
    "PrefixArchiver",
    "human_bytes",
]
