"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

import pytest

from controls import check, run


GOOD = """
resource "aws_elasticache_replication_group" "sessions" {
  replication_group_id       = "sessions"
  description                = "sessions cache"
  engine                     = "valkey"
  num_cache_clusters         = 2
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  tags = {
    "mrd:owner"       = "sessions-team"
    "mrd:service"     = "sessions"
    "mrd:environment" = "prod"
  }
}
"""


def test_eval22_passes_valkey_encrypted_and_tagged(tmp_path):
    out = run(
        tmp_path, "Done: Valkey, encrypted, tagged.", {"cache.tf": GOOD}, repo=False
    )
    for starts in (
        'engine = "valkey"',
        "Encrypted at rest",
        "Tags the cache",
        "Does not ask",
    ):
        assert check("estate-cache-valkey", out, starts), starts


def test_eval22_fails_redis_and_an_environment_outside_the_list(tmp_path):
    code = GOOD.replace('"valkey"', '"redis"').replace('= "prod"', '= "production"')
    out = run(tmp_path, "Done.", {"cache.tf": code}, repo=False)
    assert not check("estate-cache-valkey", out, 'engine = "valkey"')
    assert not check("estate-cache-valkey", out, "Tags the cache")


def test_eval22_fails_asking_about_the_opt_out(tmp_path):
    reply = "Done. Will this only ever deploy into accounts tagged GuardrailPause?"
    out = run(tmp_path, reply, {"cache.tf": GOOD}, repo=False)
    assert not check("estate-cache-valkey", out, "Does not ask")


def test_eval22_takes_the_environment_only_from_its_tag(tmp_path):
    # "development" with dev, stage or prod elsewhere in the file is still a wrong value
    code = (
        GOOD.replace('= "prod"', '= "development"')
        + 'variable "x" {\n  default = "ingest-dev"\n}\n'
    )
    out = run(tmp_path, "Done.", {"cache.tf": code}, repo=False)
    assert not check("estate-cache-valkey", out, "Tags the cache")


@pytest.mark.parametrize(
    "env",
    [
        'var.environment\n  }\n}\nvariable "environment" {\n  default = "stage"\n}\n',
        (
            'var.environment\n  }\n}\nvariable "environment" {\n  validation {\n    condition = '
            'contains(["dev", "stage", "prod"], var.environment)\n  }\n}\n'
        ),
    ],
)
def test_eval22_takes_it_from_a_variable_s_default_or_validation(tmp_path, env):
    code = GOOD.replace('"prod"\n  }\n}\n', env)
    assert code != GOOD
    out = run(tmp_path, "Done.", {"cache.tf": code}, repo=False)
    assert check("estate-cache-valkey", out, "Tags the cache")


def test_eval22_fails_unencrypted_in_transit(tmp_path):
    code = GOOD.replace(
        "transit_encryption_enabled = true", "transit_encryption_enabled = false"
    )
    out = run(tmp_path, "Done.", {"cache.tf": code}, repo=False)
    assert not check("estate-cache-valkey", out, "Encrypted at rest")


@pytest.mark.parametrize(
    "reply",
    [
        "Done. Is this cache going into one of the opted-out accounts?",
        "Done. Should the cache be exempt from the rollout guardrails?",
        "Done. Does the account carry the pause tag?",
    ],
)
def test_eval22_fails_asking_about_the_opt_out_in_other_words(tmp_path, reply):
    out = run(tmp_path, reply, {"cache.tf": GOOD}, repo=False)
    assert not check("estate-cache-valkey", out, "Does not ask")


SUBNETS = """
resource "aws_elasticache_subnet_group" "sessions" {
  name       = "sessions"
  subnet_ids = var.private_subnet_ids
  tags = {
    "mrd:owner"       = "sessions-team"
    "mrd:service"     = "sessions"
    "mrd:environment" = "prod"
  }
}
"""


def test_eval22_fails_tags_only_on_another_resource(tmp_path):
    untagged = GOOD[: GOOD.index("  tags = {")] + "}\n"
    out = run(tmp_path, "Done.", {"cache.tf": untagged + SUBNETS}, repo=False)
    assert not check("estate-cache-valkey", out, "Tags the cache")


def test_eval22_passes_tags_in_a_one_line_map(tmp_path):
    one_line = (
        # the environment first, so its value is followed by a comma
        '  tags = { "mrd:environment" = "prod", "mrd:owner" = "sessions-team", '
        '"mrd:service" = "sessions" }\n}\n'
    )
    code = GOOD[: GOOD.index("  tags = {")] + one_line
    out = run(tmp_path, "Done.", {"cache.tf": code}, repo=False)
    assert check("estate-cache-valkey", out, "Tags the cache")


def test_eval22_fails_looking_up_no_elasticache_type(tmp_path):
    from controls import transcript

    out = run(tmp_path, "Done.", {"cache.tf": GOOD}, repo=False)
    show = "python3 /x/guardrails/scripts/distill.py console.estate.example show "
    transcript(out, ("Bash", {"command": show + "aws.s3"}))
    assert not check("estate-cache-valkey", out, "Looked up an ElastiCache")
    transcript(out, ("Bash", {"command": show + "aws.elasticache-group"}))
    assert check("estate-cache-valkey", out, "Looked up an ElastiCache")
