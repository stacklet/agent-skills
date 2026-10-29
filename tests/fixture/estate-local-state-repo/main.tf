resource "aws_sqs_queue" "ingest_legacy" {
  name = "ingest-legacy"
  tags = {
    Owner             = "lab-team"
    "mrd:owner"       = "lab-team"
    "mrd:service"     = "ingest"
    "mrd:environment" = "dev"
  }
}
