variable "user_name" {
  description = "Name of the IAM user and its inline policy"
  type        = string
  default     = "email-exporter"
}

variable "mail_archive_bucket_arn" {
  description = "Bucket the exporter may write messages and sync state to"
  type        = string
}
