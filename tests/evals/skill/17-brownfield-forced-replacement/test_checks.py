"""Controls for this eval's checks: the consumer must survive the queue's replacement."""

from controls import check, run


FIFO = """resource "aws_kms_key" "orders" {}

resource "aws_sqs_queue" "orders_dlq" {
  name       = "orders-dlq.fifo"
  fifo_queue = true
}

resource "aws_sqs_queue" "orders" {
  name              = "orders.fifo"
  fifo_queue        = true
  kms_master_key_id = aws_kms_key.orders.arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.orders_dlq.arn
    maxReceiveCount     = 5
  })
  tags = merge(local.finops_tags, { team = "orders" })
}
"""
REPLY_17 = "Converted the orders queue to FIFO; this replaces the queue. Gave it a CMK, a FIFO DLQ and the required tags. Nothing else changed."


def drop_mapping(t):
    return t[: t.index('resource "aws_lambda_event_source_mapping"')]


def test_eval17_fails_a_dropped_event_source_mapping(tmp_path):
    out = run(tmp_path, REPLY_17, {"queue.tf": FIFO, "consumer.tf": drop_mapping})
    assert not check(
        "brownfield-forced-replacement", out, "The consumer Lambda still reads"
    )
    assert not check("brownfield-forced-replacement", out, "Changed nothing else")


def test_eval17_passes_a_replacement_that_keeps_the_consumer(tmp_path):
    out = run(tmp_path, REPLY_17, {"queue.tf": FIFO})
    assert check(
        "brownfield-forced-replacement", out, "The consumer Lambda still reads"
    )
    assert check("brownfield-forced-replacement", out, "Changed nothing else")
