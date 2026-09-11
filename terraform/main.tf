# The identity main.py runs as on the host: a long-lived access key, because
# the exporter is a Debian box outside AWS with nothing to assume a role with.

resource "aws_iam_user" "this" {
  name = var.user_name
}

resource "aws_iam_access_key" "this" {
  user = aws_iam_user.this.name
}

# No s3:DeleteObject anywhere, so a stolen client key cannot destroy the
# archive; the Lambda in bucket-archiver is the only identity that deletes.
# Writes are scoped by suffix rather than by prefix -- an earlier "*/email/*"
# stopped matching the moment the key layout changed, whereas ".eml.zst" cannot
# accidentally cover a tar, a manifest or the archiver's own config.
resource "aws_iam_user_policy" "this" {
  name = "${var.user_name}-access"
  user = aws_iam_user.this.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "UploadMailAndMetadata"
        Effect = "Allow"
        Action = ["s3:PutObject"]
        # Emails and their co-located metadata sidecars, wherever they sit.
        Resource = [
          for suffix in ["*.eml.zst", "*.eml.zst.json"] :
          "${var.mail_archive_bucket_arn}/${suffix}"
        ]
      },
      {
        Sid      = "ReadWriteSyncState"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${var.mail_archive_bucket_arn}/*/state.json"
      },
    ]
  })
}
