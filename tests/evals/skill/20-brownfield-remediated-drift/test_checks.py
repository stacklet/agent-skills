"""Controls for this eval's checks: the drift is a claim, not a scatter of keywords."""

import re

import pytest

from controls import check, run


LIFECYCLE = {
    "docs.tf": lambda t: (
        t
        + """
resource "aws_s3_bucket_lifecycle_configuration" "public_docs" {
  bucket = aws_s3_bucket.public_docs.id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
  }
}
"""
    )
}
# written to hit the old regexes ("automatic", "next apply", "exception", "align") while saying nothing
EMPTY_20 = (
    "Added an `aws_s3_bucket_lifecycle_configuration` that expires objects after 90 days. Lifecycle "
    "rules are applied automatically by S3 once the configuration exists.\n\nExpected plan: the next "
    "apply creates one resource; nothing else changes.\n\nThe docs bucket is public by design, which "
    "is an exception to the CIS public-access guardrail; I kept that aligned with what's there."
)
# condensed from a real reply that explained it
GOOD_20 = (
    "Added the 90-day lifecycle rule.\n\n"
    "The CIS baseline policy `cis:s3-block-public-access` applies to every AWS account. When a bucket "
    "allows public ACLs, it turns the public access block back on. This bucket sets all four to "
    "`false` on purpose, so the platform has probably already turned them on in AWS. Your next "
    "`terraform apply`, including this one, will set them back to `false`, and the platform will turn "
    "them on again. To settle it, either exempt the bucket on the platform or move to CloudFront."
)
CHECKS_20 = (
    "Final response says the platform acts",
    "Final response says an apply",
    "Final response says how to settle it",
)


@pytest.mark.parametrize("starts", CHECKS_20)
def test_eval20_fails_a_reply_that_never_explains_the_drift(tmp_path, starts):
    out = run(tmp_path, EMPTY_20, LIFECYCLE)
    assert not check("brownfield-remediated-drift", out, starts)


@pytest.mark.parametrize("starts", CHECKS_20)
def test_eval20_passes_a_reply_that_explains_it(tmp_path, starts):
    out = run(tmp_path, GOOD_20, LIFECYCLE)
    assert check("brownfield-remediated-drift", out, starts)


def test_eval20_fails_settle_on_as_the_platform_acting(tmp_path):
    reply = "Added the lifecycle rule. To settle it, the bucket needs an exemption on the platform."
    out = run(tmp_path, reply, LIFECYCLE)
    assert not check(
        "brownfield-remediated-drift", out, "Final response says the platform acts"
    )


def test_eval20_passes_deploy_wording(tmp_path):
    reply = GOOD_20.replace(
        "Your next `terraform apply`, including this one, will set them back to `false`",
        "Deploying this from Terraform sets them back to `false` again",
    )
    out = run(tmp_path, reply, LIFECYCLE)
    assert check("brownfield-remediated-drift", out, "Final response says an apply")


def flags(*names):
    def edit(t):
        t = LIFECYCLE["docs.tf"](t)
        for n in names:
            t = re.sub(n + r"(\s*)= false", n + r"\1= true", t)
        return t

    return {"docs.tf": edit}


ACLS_ON = (
    "Added the lifecycle rule. I also set block_public_acls to true so the code matches what the "
    "platform has already set; public reads come from the bucket policy, not ACLs."
)


def test_eval20_passes_turning_the_acl_flags_on_and_saying_so(tmp_path):
    out = run(tmp_path, ACLS_ON, flags("block_public_acls", "ignore_public_acls"))
    assert check(
        "brownfield-remediated-drift", out, "Left the bucket's public reads alone"
    )


def test_eval20_fails_turning_the_acl_flags_on_silently(tmp_path):
    out = run(tmp_path, "Added the lifecycle rule.", flags("block_public_acls"))
    assert not check(
        "brownfield-remediated-drift", out, "Left the bucket's public reads alone"
    )


def test_eval20_fails_blocking_the_bucket_policy(tmp_path):
    out = run(tmp_path, ACLS_ON, flags("block_public_acls", "block_public_policy"))
    assert not check(
        "brownfield-remediated-drift", out, "Left the bucket's public reads alone"
    )
