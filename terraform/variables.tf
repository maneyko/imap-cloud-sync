variable "user_name" {
  description = "Name of the IAM user and its inline policy"
  type        = string
  default     = "imap-sync-uploader"
}

variable "mail_archive_bucket_arn" {
  description = "Bucket the uploader may write messages and sync state to"
  type        = string
}
