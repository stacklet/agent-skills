"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

import shutil

from controls import check, run
from grading import FIXTURES


REPO = FIXTURES / "estate-local-state-repo"
SCOPE = ".stacklet/guardrails/scope.json"
RECORDED = '{"accounts": [{"provider": "AWS", "key": "456789012345"}]}'
NEW = """
locals {
  tags = {
    Owner             = "lab-team"
    "mrd:owner"       = "lab-team"
    "mrd:service"     = "retry"
    "mrd:environment" = "dev"
  }
}

resource "aws_sqs_queue" "retry_dlq" {
  name = "retry-dlq"
  tags = local.tags
}

resource "aws_sqs_queue" "retry" {
  name = "retry"
  tags = local.tags
}
"""
WARNED = (
    "This account's remediation deletes queues without an Owner tag, so both carry one."
)


def repo(tmp_path, reply, edits):
    out = run(tmp_path, reply, edits, repo=False)
    shutil.copytree(REPO, out, dirs_exist_ok=True)
    return out


def test_eval27_passes_finding_the_account_and_tagging(tmp_path):
    out = repo(tmp_path, WARNED, {"retry.tf": NEW, SCOPE: RECORDED})
    for starts in (
        "Recorded the local state",
        "Every new queue",
        "Warns that",
        "Tags new queues",
    ):
        assert check("estate-account-in-local-state", out, starts), starts


def test_eval27_fails_missing_the_account_and_the_owner_tag(tmp_path):
    code = NEW.replace('    Owner             = "lab-team"\n', "")
    out = repo(tmp_path, "Added the retry queue and its DLQ.", {"retry.tf": code})
    assert not check("estate-account-in-local-state", out, "Recorded the local state")
    assert not check("estate-account-in-local-state", out, "Every new queue")
    assert not check("estate-account-in-local-state", out, "Warns that")


def test_eval27_the_fixture_s_own_tagged_queue_does_not_pass(tmp_path):
    # its existing queue has Owner and every mrd: tag: an untagged new one must still fail
    code = NEW.replace("tags = local.tags", "")
    out = repo(tmp_path, WARNED, {"retry.tf": code, SCOPE: RECORDED})
    assert not check("estate-account-in-local-state", out, "Every new queue")
    assert not check("estate-account-in-local-state", out, "Tags new queues")
    # nor does writing nothing
    (tmp_path / "none").mkdir()
    out = repo(tmp_path / "none", WARNED, {SCOPE: RECORDED})
    assert not check("estate-account-in-local-state", out, "Every new queue")
    assert not check("estate-account-in-local-state", out, "Tags new queues")


def test_eval27_takes_tags_from_a_variable_s_default(tmp_path):
    code = (
        NEW.replace('"mrd:environment" = "dev"', '"mrd:environment" = var.env')
        + 'variable "env" {\n  default = "stage"\n}\n'
    )
    out = repo(tmp_path, WARNED, {"retry.tf": code, SCOPE: RECORDED})
    assert check("estate-account-in-local-state", out, "Tags new queues")
