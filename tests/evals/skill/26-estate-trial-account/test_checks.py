"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import check, run


GOOD = """
locals {
  tags = {
    Owner             = "lab-team"
    "mrd:owner"       = "lab-team"
    "mrd:service"     = "ingest"
    "mrd:environment" = "dev"
  }
}

resource "aws_sqs_queue" "ingest_dlq" {
  name = "ingest-dlq"
  tags = local.tags
}

resource "aws_sqs_queue" "ingest" {
  name = "ingest"
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingest_dlq.arn
    maxReceiveCount     = 5
  })
  tags = local.tags
}
"""
SCOPE = ".stacklet/guardrails/scope.json"
RECORDED = '{"accounts": [{"provider": "AWS", "key": "456789012345"}]}'
WARNED = (
    "This account's remediation deletes queues without an Owner tag, so both carry one."
)


def test_eval26_passes_tagging_owner_and_warning(tmp_path):
    out = run(tmp_path, WARNED, {"sqs.tf": GOOD, SCOPE: RECORDED}, repo=False)
    for starts in (
        "Recorded the provider",
        "Every queue",
        "Warns that",
        "Tags every queue",
    ):
        assert check("estate-trial-account", out, starts), starts


def test_eval26_fails_a_queue_without_owner_and_no_warning(tmp_path):
    code = GOOD.replace('    Owner             = "lab-team"\n', "")
    out = run(tmp_path, "Done: a queue and its DLQ.", {"sqs.tf": code}, repo=False)
    assert not check("estate-trial-account", out, "Every queue")
    assert not check("estate-trial-account", out, "Warns that")
    assert not check("estate-trial-account", out, "Recorded the provider")


def test_eval26_fails_only_the_mrd_owner_tag(tmp_path):
    # "mrd:owner" is another tag: the remediation looks for Owner, exactly
    code = GOOD.replace('    Owner             = "lab-team"\n', "")
    reply = "Tagged mrd:owner; untagged queues are deleted in this account."
    out = run(tmp_path, reply, {"sqs.tf": code}, repo=False)
    assert not check("estate-trial-account", out, "Every queue")
    assert not check("estate-trial-account", out, "Warns that")


def test_eval26_passes_owner_through_default_tags_or_merge(tmp_path):
    plain = GOOD.replace('    Owner             = "lab-team"\n', "")
    via_default = (
        'provider "aws" {\n  default_tags {\n    tags = {\n      Owner = "lab-team"\n    }\n'
        "  }\n}\n" + plain
    )
    via_merge = GOOD.replace(
        "tags = local.tags", 'tags = merge(local.tags, { Name = "q" })'
    )
    for i, code in enumerate((via_default, via_merge)):
        (tmp_path / str(i)).mkdir()
        out = run(
            tmp_path / str(i), WARNED, {"sqs.tf": code, SCOPE: RECORDED}, repo=False
        )
        assert check("estate-trial-account", out, "Every queue"), i


def test_eval26_fails_mrd_tags_only_on_another_resource(tmp_path):
    # the queues take only Owner; the mrd: tags sit on an alarm topic beside them
    code = (
        GOOD.replace("tags = local.tags", 'tags = { Owner = "lab-team" }')
        + '\nresource "aws_sns_topic" "alarms" {\n  name = "ingest-alarms"\n'
        "  tags = local.tags\n}\n"
    )
    out = run(tmp_path, WARNED, {"sqs.tf": code, SCOPE: RECORDED}, repo=False)
    assert check("estate-trial-account", out, "Every queue has an Owner")
    assert not check("estate-trial-account", out, "Tags every queue")
