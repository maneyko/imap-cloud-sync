from pathlib import Path
from lib.util import save_to_s3, read_from_s3

app_root = Path(__file__).resolve().parent.parent

class FileStore:
    def __init__(self, *_):
        pass

    def write(self, path, data, **_):
        path = Path(path)
        if not path.is_absolute():
            path = app_root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        elif isinstance(data, str):
            path.write_text(data)
        else:
            raise RuntimeError(f"Invalid argument type: {type(data)}")

    def read(self, path) -> bytes:
        path = Path(path)
        if not path.is_absolute():
            path = app_root / path
        return path.read_bytes()


class S3Store:
    # Let's calculate the exact break-even point mathematically to verify and provide the exact equation.
    # Prices based on us-east-1 (Standard: $0.023 / GB, Deep Archive: $0.00099 / GB)
    GB_in_kb = 1024 * 1024

    P_std = 0.023 / GB_in_kb # Price of Standard per KB
    P_da = 0.00099 / GB_in_kb # Price of Deep Archive per KB

    # Deep Archive monthly cost for file size S (in KB):
    # Cost_DA = (S + 32) * P_da + 8 * P_std
    # Standard monthly cost for file size S (in KB):
    # Cost_Std = S * P_std

    # Break-even when Cost_DA = Cost_Std:
    # S * P_std = (S + 32) * P_da + 8 * P_std
    # S * P_std - S * P_da = 32 * P_da + 8 * P_std
    # S * (P_std - P_da) = 32 * P_da + 8 * P_std
    # S = (32 * P_da + 8 * P_std) / (P_std - P_da)

    MIN_SIZE_BYTES = int((32 * P_da + 8 * P_std) / (P_std - P_da) * 1024)
    # Meaning a file that is 10,034 bytes costs the same to store in STANDARD as DEEP_ARCHIVE.

    def __init__(self, bucket_name):
        self.bucket_name = bucket_name

    def write(self, path, data, **kwargs):
        return save_to_s3(self.bucket_name, path, data, **kwargs)

    def read(self, path):
        return read_from_s3(self.bucket_name, path)
