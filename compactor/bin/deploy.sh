#!/bin/bash

set -e
cd "$(dirname "$0")/../"
__DIR__=$PWD

export AWS_PROFILE="personal"

lambda_name="imap-sync-s3-archiver"

BUCKET="my-lambdas"
KEY="$lambda_name/function.zip"
ZIP="dist/$lambda_name.zip"

DEPLOY_ITEMS=(
  lib
  main.py
  config.toml
)

pyclean() {
  find .        -type f -name '*.py[co]'    -delete
  find . -depth -type d -name '__pycache__' -delete
}

d=dist/package
rm -fr "$d" && mkdir -p "$d" && cd "$d"

for f in ${DEPLOY_ITEMS[@]}; do
  cp -r "$__DIR__/$f" .
done

pyclean
zip -r "$__DIR__/$ZIP" .
cd "$OLDPWD" && rm -fr "$d"

if [[ $1 =~ ^(-b|--build)$ ]]; then
  echo "built only (--build); not uploading"
  exit 0
fi

checksum=$(sha256sum "$ZIP" | awk '{print $1}')

aws s3api put-object \
  --bucket $BUCKET --key $KEY --body "$ZIP" \
  --content-type application/zip \
  --metadata "sha256=$checksum"

echo "uploaded s3://$BUCKET/$KEY"

aws lambda update-function-code \
  --function-name $lambda_name \
  --s3-bucket $BUCKET \
  --s3-key $KEY

echo "Lambda synced"
