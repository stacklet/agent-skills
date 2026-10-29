resource "aws_sqs_queue" "orders" {
  name                       = "orders"
  visibility_timeout_seconds = 30
  message_retention_seconds  = 345600
  sqs_managed_sse_enabled    = true

  tags = {
    team = "orders"
  }
}
