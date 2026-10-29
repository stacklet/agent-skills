"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import check, run


NEW_BUCKET = """
resource "aws_kms_key" "finance" {}

resource "aws_s3_bucket" "finance_exports" {
  bucket = "acme-orders-finance-exports"
  tags   = merge(local.finops_tags, { data-classification = "confidential" })
}

resource "aws_s3_bucket_public_access_block" "finance_exports" {
  bucket                  = aws_s3_bucket.finance_exports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "finance_exports" {
  bucket = aws_s3_bucket.finance_exports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.finance.arn
    }
  }
}
"""
REPLY_13 = (
    "Added the finance exports bucket with a customer-managed key and the required tags.\n\n"
    "The reports bucket still uses AES256 and lacks cost-center; I left it alone."
)


def test_eval13_passes_a_compliant_bucket_beside_the_old_one(tmp_path):
    out = run(tmp_path, REPLY_13, {"storage.tf": lambda t: t + NEW_BUCKET})
    assert check("brownfield-new-beside-old", out, "Existing resources untouched")
    assert check(
        "brownfield-new-beside-old",
        out,
        "Final response tells the user the reports bucket",
    )


def test_eval13_fails_an_unrelated_resource_added_alongside(tmp_path):
    extra = NEW_BUCKET + '\nresource "aws_sqs_queue" "exports_ready" {}\n'
    out = run(tmp_path, REPLY_13, {"storage.tf": lambda t: t + extra})
    assert not check("brownfield-new-beside-old", out, "Existing resources untouched")


def test_eval13_fails_reports_and_aes256_said_in_different_places(tmp_path):
    reply = "Added the finance exports bucket next to reports.\n\nIt avoids AES256."
    out = run(tmp_path, reply, {"storage.tf": lambda t: t + NEW_BUCKET})
    assert not check(
        "brownfield-new-beside-old",
        out,
        "Final response tells the user the reports bucket",
    )
