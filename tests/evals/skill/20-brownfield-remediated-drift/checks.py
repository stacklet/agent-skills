from grading import (
    PLATFORM,
    has,
    nocomments,
    prose_lines,
    said_together,
    unexpected_changes,
)


SHOWS = ("aws.s3",)


def checks(d, t, f):
    tc = nocomments(t)
    # Turning the ACL flags back on makes the code match what the platform set, one of the two
    # settlements SKILL.md names, and leaves public reads (through the bucket policy) working; the
    # other two flags would block them. Only if the reply says so.
    acls = said_together(
        f, r"block_public_acls|ignore_public_acls|\bACLs?\b", r"\btrue\b|\bon\b|match"
    )
    bad = unexpected_changes(
        d,
        {
            "aws_s3_bucket_public_access_block.public_docs": (
                r"^(block_public_acls|ignore_public_acls) = (true|false)$"
                if acls
                else "^$"
            )
        },
        new=r"aws_s3_bucket_lifecycle_configuration|aws_s3_bucket_server_side_encryption_configuration",
    )
    return [
        (
            "Added a lifecycle rule expiring objects after 90 days on the docs bucket",
            has(
                r'resource\s+"aws_s3_bucket_lifecycle_configuration"[^{]*\{.*?aws_s3_bucket\.public_docs.*?days\s*=\s*90',
                tc,
            ),
        ),
        (
            "Left the bucket's public reads alone (the bucket policy, and the two flags that would block it; "
            "turning the ACL flags on is fine if the reply says so): " + "; ".join(bad),
            not bad,
        ),
        (
            "Final response says the platform acts on this bucket (set-public-block puts the block back)",
            has(r"set-public-block", f)
            or said_together(
                f,
                PLATFORM,
                r"((turn|switch|put)\w*|\bsets?)\b.{0,40}\b(on|back)\b|re-?(enabl|appl)|lock\w* .{0,20}down",
                within=prose_lines,
            ),
        ),
        (
            "Final response says an apply (this one included) undoes the platform's change to the bucket",
            said_together(
                f,
                r"\bappl(y|ying|ies)\b|\bdeploy\w*",
                r"undo|revert|back|again|\boff\b|false|re-?open|reset|flip|toggle",
            ),
        ),
        (
            "Final response says how to settle it (exempt the bucket, or make the code match the platform)",
            has(
                r"\bexempt|(get|request|ask for|need) an? (exception|exemption)|an? (exception|exemption) (for|on)\b"
                r"|change (the )?code"
                r"|match(es)? (the )?(code|platform)|cloudfront|origin access",
                f,
            ),
        ),
    ]
