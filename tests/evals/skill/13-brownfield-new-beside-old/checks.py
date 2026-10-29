from grading import (
    TAGS_OK,
    added_text,
    drift_sentences,
    has,
    nocomments,
    prose_lines,
    said_together,
    unexpected_changes,
)


SHOWS = ("aws.s3",)


def checks(d, t, f):
    tc = nocomments(t)
    bad = unexpected_changes(d, new=r"aws_s3_bucket\w*|aws_kms_\w+")
    return [
        (
            "Mentions the unrelated docs bucket's drift in two sentences at most",
            drift_sentences(f) <= 2,
        ),
        (
            "Added a new S3 bucket",
            has(r'resource\s+"aws_s3_bucket"\s', added_text(d)),
        ),
        (
            "New bucket uses SSE-KMS with a customer-managed key (not the neighbour's AES256, not alias/aws/s3)",
            has(r"aws:kms", tc)
            and has(r"kms_master_key_id\s*=", tc)
            and not has(r"alias/aws/s3", tc)
            and not has(r'"AES256"', nocomments(added_text(d))),
        ),
        (
            "New bucket has required tags with a valid value (literal, variable, or the repo's local.finops_tags)",
            TAGS_OK(added_text(d)) or has(r"local\.finops_tags", added_text(d)),
        ),
        (
            "New bucket has a public access block",
            has(r'resource\s+"aws_s3_bucket_public_access_block"', added_text(d)),
        ),
        (
            "Existing resources untouched but for repo-valued tags (the reports bucket's encryption isn't changed unasked): "
            + "; ".join(bad),
            not bad,
        ),
        (
            "Final response tells the user the reports bucket misses the CMK/tag guardrails",
            said_together(
                f,
                r"reports",
                r"AES256|SSE-S3|S3[- ]managed|CMK|customer[- ]managed|KMS|cost-center|data-classification|tag",
                within=prose_lines,
            ),
        ),
    ]
