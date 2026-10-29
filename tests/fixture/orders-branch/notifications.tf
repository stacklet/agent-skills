resource "aws_sqs_queue" "notifications" {
  name                      = "order-notifications"
  message_retention_seconds = 345600

  tags = {
    team = "orders"
  }
}
