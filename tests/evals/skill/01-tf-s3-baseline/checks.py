import re

from grading import TAGS_OK, has, mentions_guardrails


SHOWS = ("aws.s3",)


def checks(d, t, f):
    return [
        (
            "Has an aws_s3_bucket_public_access_block with all four flags true",
            has(r"aws_s3_bucket_public_access_block", t)
            and len(
                re.findall(
                    r"(block_public_acls|block_public_policy|ignore_public_acls|restrict_public_buckets)\s*=\s*true",
                    t,
                )
            )
            >= 4,
        ),
        (
            "Configures server-side encryption on the bucket",
            has(r"server_side_encryption_configuration", t),
        ),
        (
            "Does not use a public-read ACL",
            not has(r'acl\s*=\s*"(public-read|public-read-write)"', t),
        ),
        (
            "Final response tells the user which guardrails applied",
            mentions_guardrails(f),
        ),
        (
            "Wrote a bucket policy for CloudFront",
            has(r"aws_s3_bucket_policy", t) and has(r"cloudfront", t),
        ),
        # the fixture's ORG rules
        (
            "Bucket uses SSE-KMS with an explicit customer-managed key, not AES256 or the default key (org rule)",
            has(r"aws:kms", t)
            and has(r"kms_master_key_id\s*=", t)
            and not has(r"sse_algorithm\s*=\s*\"AES256\"", t),
        ),
        (
            "Tags cost-center and data-classification with a valid value (org rule)",
            TAGS_OK(t),
        ),
    ]
