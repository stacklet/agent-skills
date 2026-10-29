"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

import json

from controls import check, run


SCOPE = ".stacklet/guardrails/scope.json"
QUEUES = """
resource "aws_sqs_queue" "order_events" {
  name = "order-events"
  tags = {
    "mrd:owner"       = "orders"
    "mrd:service"     = "order-events"
    "mrd:environment" = "prod"
  }
}
"""


def scope(*keys, because="providers.tf"):
    return json.dumps(
        {"accounts": [{"provider": "AWS", "key": k, "because": because} for k in keys]}
    )


def test_eval28_passes_recording_only_the_deploy_target(tmp_path):
    out = run(
        tmp_path, "Done.", {"sqs.tf": QUEUES, SCOPE: scope("210987654321")}, repo=False
    )
    for starts in (
        "Recorded the account the provider",
        "Did not record",
        "Tags every queue",
    ):
        assert check("estate-profile-elsewhere", out, starts), starts


def test_eval28_fails_also_recording_the_profile_s_account(tmp_path):
    keys = ("210987654321", "456789012345")
    out = run(tmp_path, "Done.", {"sqs.tf": QUEUES, SCOPE: scope(*keys)}, repo=False)
    assert check("estate-profile-elsewhere", out, "Recorded the account the provider")
    assert not check("estate-profile-elsewhere", out, "Did not record")


def test_eval28_fails_recording_nothing(tmp_path):
    # not recording the profile's account by recording no account at all is no judgement
    out = run(tmp_path, "Done.", {"sqs.tf": QUEUES}, repo=False)
    assert not check(
        "estate-profile-elsewhere", out, "Recorded the account the provider"
    )
    assert not check("estate-profile-elsewhere", out, "Did not record")


def test_eval28_passes_naming_the_profile_s_account_in_the_reason(tmp_path):
    # as real runs put it: the reason says why the other account was left out
    because = "assume_role in providers.tf; the lab profile's 456789012345 only supplies credentials"
    files = {"sqs.tf": QUEUES, SCOPE: scope("210987654321", because=because)}
    out = run(tmp_path, "Done.", files, repo=False)
    assert check("estate-profile-elsewhere", out, "Did not record")


def test_eval28_fails_tags_only_on_another_resource(tmp_path):
    code = (
        QUEUES[: QUEUES.index("  tags = {")]
        + "}\n"
        + QUEUES.replace("aws_sqs_queue", "aws_sns_topic")
    )
    out = run(
        tmp_path, "Done.", {"sqs.tf": code, SCOPE: scope("210987654321")}, repo=False
    )
    assert not check("estate-profile-elsewhere", out, "Tags every queue")


def test_eval28_passes_tags_in_a_one_line_map(tmp_path):
    code = (
        QUEUES[: QUEUES.index("  tags = {")]
        + '  tags = { "mrd:owner" = "orders", "mrd:environment" = "prod", '
        '"mrd:service" = "order-events" }\n}\n'
    )
    out = run(
        tmp_path, "Done.", {"sqs.tf": code, SCOPE: scope("210987654321")}, repo=False
    )
    assert check("estate-profile-elsewhere", out, "Tags every queue")
