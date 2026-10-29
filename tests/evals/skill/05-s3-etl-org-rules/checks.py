import re

from grading import TAGS_OK, has


SHOWS = ("aws.s3",)


def checks(d, t, f):
    return [
        (
            "Bucket uses SSE-KMS with an explicit customer-managed key, not AES256 or the default key",
            has(r"aws:kms", t)
            and has(r"kms_master_key_id\s*=", t)
            and not has(r"sse_algorithm\s*=\s*\"AES256\"", t),
        ),
        ("Tags cost-center and data-classification with a valid value", TAGS_OK(t)),
        (
            "Public access block present with all four flags true",
            len(
                re.findall(
                    r"(block_public_acls|block_public_policy|ignore_public_acls|restrict_public_buckets)\s*=\s*true",
                    t,
                )
            )
            >= 4,
        ),
    ]
