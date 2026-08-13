#!/bin/bash

# Build and upload the S3 archiver Lambda deployment package.
#
#   ./deploy_lambda.sh            # build + upload, print the new version id
#   ./deploy_lambda.sh --build    # build only, no upload
#
# Then apply Terraform to point the function at the new package:
#   cd ../terraform-aws/terraform && terraform apply
#
# The zip is built from an explicit file list, never from a directory walk, so
# the IMAP credentials in secrets/ cannot be packaged by accident. Entries use a
# fixed timestamp so identical source always produces an identical zip, which
# keeps Terraform from seeing spurious code changes.

set -euo pipefail
cd "$(dirname "$0")"

export AWS_PROFILE="personal"

BUCKET="my-lambdas"
KEY="imap-sync-s3-archiver/function.zip"
ZIP="dist/imap-sync-s3-archiver.zip"
EXPECTED_ACCOUNT="123456789012"

mkdir -p dist
python3 - "$ZIP" << 'PY'
import hashlib, pathlib, sys, zipfile

out = pathlib.Path(sys.argv[1])
files = ["main_lambda.py"] + sorted(str(p) for p in pathlib.Path("lib_lambda").glob("*.py"))

for name in files:
    if not pathlib.Path(name).is_file():
        sys.exit(f"missing expected source file: {name}")

out.unlink(missing_ok=True)
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for name in files:
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, pathlib.Path(name).read_bytes())

print(f"built {out} ({out.stat().st_size:,} bytes) from {len(files)} files:")
for name in files:
    print(f"  {name}")
print("sha256:", hashlib.sha256(out.read_bytes()).hexdigest())
PY

# Fail loudly if anything sensitive ever ends up in the package.
if unzip -Z1 "$ZIP" | grep -Ei 'secret|\.toml$|getmailrc|\.env'; then
  echo "ERROR: package contains sensitive-looking files, refusing to upload" >&2
  exit 1
fi

if [[ ${1:-} == --build ]]; then
  echo "built only (--build); not uploading"
  exit 0
fi

VERSION_ID=$(aws s3api put-object \
  --bucket "$BUCKET" --key "$KEY" --body "$ZIP" \
  --content-type application/zip \
  --metadata "sha256=$(shasum -a 256 "$ZIP" | cut -d' ' -f1)" \
  --query VersionId --output text)

echo "uploaded s3://$BUCKET/$KEY"
echo "version id: $VERSION_ID"
