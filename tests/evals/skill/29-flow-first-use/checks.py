import re

from grading import (
    TAGS_OK,
    all_exact,
    before,
    fetches,
    has,
    is_kind,
    loaded,
    refetched,
    written_after,
)


SHOWS = ("aws.s3",)


def checks(d, t, f):
    return [
        (
            "Has an aws_s3_bucket_public_access_block with all four flags true",
            len(
                re.findall(
                    r"(block_public_acls|block_public_policy|ignore_public_acls|restrict_public_buckets)\s*=\s*true",
                    t,
                )
            )
            >= 4,
        ),
        (
            "Bucket uses SSE-KMS with an explicit customer-managed key (org rule)",
            has(r"aws:kms", t) and has(r"kms_master_key_id\s*=", t),
        ),
        (
            "Tags cost-center and data-classification with a valid value (org rule)",
            TAGS_OK(t),
        ),
    ]


def server_checks(log, state, calls):
    return [
        ("Fetched the posture, once", len(fetches(log, "posture")) == 1),
        (
            "Fetched the posture before any collection",
            before(log, is_kind("posture"), is_kind("collections")),
        ),
        ("Fetched no collection twice", not refetched(log)),
        ("Sent only the skill's own queries", all_exact(log)),
        (
            "Ingested the collections that apply by default (CIS, Acme Org Standards)",
            loaded(state, "CIS AWS Baseline") and loaded(state, "Acme Org Standards"),
        ),
        (
            "Wrote the bucket only after the collections were ingested",
            written_after(calls, "collections", r'resource\s+"aws_s3_bucket"'),
        ),
    ]
