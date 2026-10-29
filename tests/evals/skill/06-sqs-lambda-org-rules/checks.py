import re

from grading import TAGS_OK, has, nocomments, numbers_set


SHOWS = ("aws.sqs", "aws.lambda")


def checks(d, t, f):
    r = re.search(r"maxReceiveCount\W+(\d+)", t) or re.search(
        r"max_receive_count\W+(\d+)", t
    )
    return [
        (
            "SQS has a redrive policy with maxReceiveCount <= 5",
            bool(r) and int(r.group(1)) <= 5,
        ),
        (
            "SQS uses a customer-managed KMS key (kms_master_key_id, not the AWS-managed alias/aws/sqs)",
            has(r"kms_master_key_id\s*=", t)
            and not has(r"alias/aws/sqs", nocomments(t)),
        ),
        (
            "Lambda tracing_config mode Active",
            has(r"tracing_config\s*\{[^}]*mode\s*=\s*\"Active\"", t),
        ),
        (
            "Lambda sets reserved_concurrent_executions (literal, or a variable whose default is a positive number)",
            any(v >= 1 for v in numbers_set("reserved_concurrent_executions", t)),
        ),
        ("Tags cost-center and data-classification with a valid value", TAGS_OK(t)),
    ]
