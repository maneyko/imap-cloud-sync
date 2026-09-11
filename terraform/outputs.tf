output "access_key_id" {
  description = "Access key id for the client-side exporter"
  value       = aws_iam_access_key.this.id
}

output "secret_access_key" {
  description = "Secret access key for the client-side exporter"
  value       = aws_iam_access_key.this.secret
  sensitive   = true
}
