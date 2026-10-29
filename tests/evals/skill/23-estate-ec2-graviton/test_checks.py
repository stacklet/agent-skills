"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import check, run


GOOD = """
data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]
  filter {
    name   = "name"
    values = ["al2023-ami-*-arm64"]
  }
}

resource "aws_instance" "worker" {
  ami           = data.aws_ami.al2023.id
  instance_type = var.instance_type
  metadata_options {
    http_tokens = "required"
  }
  root_block_device {
    volume_type = "gp3"
    encrypted   = true
  }
  tags = local.tags
}

variable "instance_type" {
  default = "m7g.large"
}

resource "aws_ebs_volume" "data" {
  size      = 100
  type      = "gp3"
  encrypted = true
  tags      = local.tags
}

locals {
  tags = {
    "mrd:owner"       = "reporting"
    "mrd:service"     = "nightly-report"
    "mrd:environment" = "prod"
  }
}
"""


def test_eval23_passes_graviton_through_a_variable(tmp_path):
    out = run(tmp_path, "Done.", {"main.tf": GOOD}, repo=False)
    for starts in (
        "A Graviton",
        "An arm64",
        "gp3 volumes",
        "The data volume",
        "IMDSv2",
        "Tags the instance",
    ):
        assert check("estate-ec2-graviton", out, starts), starts


def test_eval23_fails_x86_and_gp2(tmp_path):
    code = (
        GOOD.replace('"m7g.large"', '"m6i.large"')
        .replace("-arm64", "-x86_64")
        .replace('type      = "gp3"', 'type      = "gp2"')
    )
    out = run(tmp_path, "Done.", {"main.tf": code}, repo=False)
    assert not check("estate-ec2-graviton", out, "A Graviton")
    assert not check("estate-ec2-graviton", out, "An arm64")
    assert not check("estate-ec2-graviton", out, "gp3 volumes")


def test_eval23_fails_encrypting_only_the_root_volume(tmp_path):
    code = GOOD.replace(
        '  type      = "gp3"\n  encrypted = true\n', '  type      = "gp3"\n'
    )
    assert code != GOOD
    out = run(tmp_path, "Done.", {"main.tf": code}, repo=False)
    assert not check("estate-ec2-graviton", out, "The data volume")


def test_eval23_counts_a_lookup_in_a_shell_loop_over_types(tmp_path):
    # as a real run did it: one loop rather than a command per type
    from controls import transcript

    out = run(tmp_path, "Done.", {"main.tf": GOOD}, repo=False)
    loop = (
        "for t in aws.ec2 aws.ebs aws.security-group; do python3 /x/guardrails/scripts/distill.py "
        'console.estate.example show "$t"; done'
    )
    transcript(out, ("Bash", {"command": loop}), ("Write", {"file_path": "main.tf"}))
    assert check("estate-ec2-graviton", out, "Looked up aws.ec2")
    assert check("estate-ec2-graviton", out, "Looked up aws.ebs")
    # a loop over something else, then a show of another type: no lookup of these
    other = (
        "for f in *.tf; do cat $f; done; python3 /x/guardrails/scripts/distill.py "
        "console.estate.example show aws.s3"
    )
    transcript(out, ("Bash", {"command": other}), ("Write", {"file_path": "main.tf"}))
    assert not check("estate-ec2-graviton", out, "Looked up aws.ec2")


def test_eval23_fails_imdsv1_allowed(tmp_path):
    code = GOOD.replace('http_tokens = "required"', 'http_tokens = "optional"')
    out = run(tmp_path, "Done.", {"main.tf": code}, repo=False)
    assert not check("estate-ec2-graviton", out, "IMDSv2")


def test_eval23_fails_tags_only_on_the_volume(tmp_path):
    code = GOOD.replace("  }\n  tags = local.tags\n}\n\nvariable", "  }\n}\n\nvariable")
    assert code != GOOD
    out = run(tmp_path, "Done.", {"main.tf": code}, repo=False)
    assert not check("estate-ec2-graviton", out, "Tags the instance")


def test_eval23_passes_tags_merged_into_a_local_from_a_validated_variable(tmp_path):
    # as a real run wrote them
    code = GOOD.replace(
        """locals {
  tags = {
    "mrd:owner"       = "reporting"
    "mrd:service"     = "nightly-report"
    "mrd:environment" = "prod"
  }
}""",
        """locals {
  tags = merge(var.extra_tags, {
    "mrd:environment" = var.environment
    "mrd:owner"       = var.owner
    "mrd:service"     = var.service
  })
}

variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "stage", "prod"], var.environment)
    error_message = "environment must be one of dev, stage, prod."
  }
}""",
    )
    assert code != GOOD
    out = run(tmp_path, "Done.", {"main.tf": code}, repo=False)
    assert check("estate-ec2-graviton", out, "Tags the instance")
