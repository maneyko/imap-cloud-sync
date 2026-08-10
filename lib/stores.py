from pathlib import Path
from lib.util import save_to_s3, read_from_s3

app_root = Path(__file__).resolve().parent.parent

class FileStore:
    def __init__(self, *_):
        pass

    def write(self, path: Path, data):
        if not path.is_absolute():
            path = app_root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        elif isinstance(data, str):
            path.write_text(data)
        else:
            raise RuntimeError(f"Invalid argument type: {type(data)}")

    def read(self, path: Path) -> bytes:
        if not path.is_absolute():
            path = app_root / path
        return path.read_bytes()


class S3Store:
    def __init__(self, bucket_name):
        self.bucket_name = bucket_name

    def write(self, path: Path, data):
        return save_to_s3(self.bucket_name, path, data)

    def read(self, path: Path):
        return read_from_s3(self.bucket_name, path)
