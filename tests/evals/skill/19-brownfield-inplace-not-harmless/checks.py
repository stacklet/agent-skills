from grading import (
    KEY,
    KEY_COST,
    block,
    classification_said,
    drift_sentences,
    has,
    known_tags_ok,
    nocomments,
    prose_lines,
    said_together,
    unexpected_changes,
)


SHOWS = ("aws.s3",)


def checks(d, t, f):
    tc = nocomments(t)
    bad = unexpected_changes(
        d,
        {
            # the reports bucket's tags are a free fix (the repo holds the values)
            "aws_s3_bucket.reports": r'^(tags\b|"?(cost-center|data-classification|team)"?\s=|\}\)?$)'
        },
        new=r"aws_s3_bucket_versioning",
    )
    return [
        (
            "Mentions the unrelated docs bucket's drift in two sentences at most",
            drift_sentences(f) <= 2,
        ),
        (
            "Versioning enabled on the reports bucket",
            has(
                r'resource\s+"aws_s3_bucket_versioning"[^{]*\{[^}]*aws_s3_bucket\.reports.*?status\s*=\s*"Enabled"',
                tc,
            ),
        ),
        (
            "Encryption left as it is (SSE-KMS would need kms:Decrypt for every reader); nothing else changed: "
            + "; ".join(bad),
            not bad,
        ),
        (
            "Required tags not invented (repo values or none)",
            known_tags_ok(nocomments(block("aws_s3_bucket", "reports", t))),
        ),
        (
            "If it gave the bucket the repo's data-classification, the reply says so (the repo's default may be wrong for this data)",
            classification_said(d, "aws_s3_bucket.reports", f),
        ),
        (
            "Final response tells the user the bucket misses the CMK guardrail",
            has(r"KMS|CMK|customer[- ]managed", f)
            and has(r"AES256|SSE-S3|S3[- ]managed", f),
        ),
        (
            "Final response says what switching would involve for readers (key permissions, kms:Decrypt)",
            said_together(f, KEY, KEY_COST + r"|reads?|writes?", within=prose_lines),
        ),
    ]
