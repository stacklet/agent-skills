"""Controls for this eval's checks: a resource added on the branch is new."""

from controls import check, run
from grading import FIXTURES


BRANCH_QUEUE = (FIXTURES / "orders-branch" / "notifications.tf").read_text()


def test_eval21_fails_treating_the_branch_queue_as_existing(tmp_path):
    reply = "Set retention to 7 days. The queue already lacks a DLQ and a CMK; want me to add them?"
    out = run(
        tmp_path,
        reply,
        {"notifications.tf": BRANCH_QUEUE.replace("345600", "604800")},
    )
    assert not check(
        "brownfield-unmerged-resource", out, "Final response says the queue is new"
    )


def test_eval21_fails_leaving_the_dlq_out_unmentioned(tmp_path):
    reply = (
        "Set retention to 7 days. This queue was added on this branch and hasn't been deployed, so "
        "I gave it the required tags."
    )
    out = run(
        tmp_path, reply, {"notifications.tf": BRANCH_QUEUE.replace("345600", "604800")}
    )
    assert not check(
        "brownfield-unmerged-resource", out, "The never-deployed queue gets a redrive"
    )


def test_eval21_passes_leaving_the_dlq_out_and_saying_so(tmp_path):
    # from a real reply: the session weighed the change's scope and reported the gap
    reply = (
        "Set retention to 7 days. This queue only exists on this branch.\n\n"
        "- **Dead-letter queue (MEDIUM):** the queue needs a redrive policy pointing to a dead-letter "
        "queue, with `maxReceiveCount` <= 5. That's a second queue, which also needs tags and "
        "encryption."
    )
    out = run(
        tmp_path, reply, {"notifications.tf": BRANCH_QUEUE.replace("345600", "604800")}
    )
    assert check(
        "brownfield-unmerged-resource", out, "The never-deployed queue gets a redrive"
    )


def test_eval21_passes_giving_the_branch_queue_its_guardrails(tmp_path):
    fixed = """resource "aws_kms_key" "notifications" {}

resource "aws_sqs_queue" "notifications_dlq" {
  name = "order-notifications-dlq"
}

resource "aws_sqs_queue" "notifications" {
  name                      = "order-notifications"
  message_retention_seconds = 604800
  kms_master_key_id         = aws_kms_key.notifications.arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.notifications_dlq.arn
    maxReceiveCount     = 5
  })
  tags = merge(local.finops_tags, { team = "orders" })
}
"""
    reply = (
        "Set retention to 7 days. This queue was added on this branch and hasn't been deployed, so "
        "I gave it the guardrails now: a customer-managed key, a DLQ and the required tags."
    )
    out = run(tmp_path, reply, {"notifications.tf": fixed})
    for starts in (
        "Notifications queue retention",
        "The never-deployed queue gets a customer-managed key",
        "The never-deployed queue gets a redrive",
        "The never-deployed queue carries",
        "Final response says the queue is new",
        "Changed nothing in the resources already on main",
    ):
        assert check("brownfield-unmerged-resource", out, starts), starts


def test_eval21_passes_declining_the_dlq_with_a_reason(tmp_path):
    reply = (
        "Set retention to 7 days. This queue was added on this branch and hasn't been deployed, so I "
        "gave it a customer-managed key and the required tags. I left out a dead-letter queue: it "
        "changes how repeatedly failing messages are handled, so that's your call."
    )
    out = run(
        tmp_path, reply, {"notifications.tf": BRANCH_QUEUE.replace("345600", "604800")}
    )
    assert check(
        "brownfield-unmerged-resource", out, "The never-deployed queue gets a redrive"
    )


def test_eval21_passes_saying_the_branch_adds_the_queue(tmp_path):
    # from a real reply
    reply = (
        "Set retention to 7 days. This branch is what adds the queue, so the plan will show a "
        "create. Since the queue is still new, it can be made compliant at no cost to anything running."
    )
    out = run(
        tmp_path, reply, {"notifications.tf": BRANCH_QUEUE.replace("345600", "604800")}
    )
    assert check(
        "brownfield-unmerged-resource", out, "Final response says the queue is new"
    )
