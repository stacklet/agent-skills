resource "aws_lambda_function" "orders_consumer" {
  function_name = "orders-consumer"
  role          = var.consumer_role_arn
  runtime       = "python3.12"
  handler       = "consumer.handler"
  filename      = "build/consumer.zip"
  timeout       = 25

  tags = merge(local.finops_tags, {
    team = "orders"
  })
}

resource "aws_lambda_event_source_mapping" "orders" {
  event_source_arn = aws_sqs_queue.orders.arn
  function_name    = aws_lambda_function.orders_consumer.arn
  batch_size       = 10
}
