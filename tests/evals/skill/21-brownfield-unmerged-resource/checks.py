import re

from grading import (
    DLQ,
    DLQ_COST,
    GAP,
    KEY,
    KEY_COST,
    block,
    classification_said,
    drift_sentences,
    has,
    nocomments,
    numbers_set,
    prose_lines,
    said_together,
    unexpected_changes,
)


SHOWS = ("aws.sqs",)


def checks(d, t, f):
    tc = nocomments(t)
    q = block("aws_sqs_queue", "notifications", tc)
    r = re.search(r"maxReceiveCount\W+(\d+)", q) or re.search(
        r"max_receive_count\W+(\d+)", q
    )
    bad = unexpected_changes(d, new=r"aws_sqs_\w+|aws_kms_\w+")
    # the queue still lacks it, or a guardrail wants it
    still = GAP + r"|needs?|requires?|must|wants?"
    return [
        (
            "Mentions the unrelated docs bucket's drift in two sentences at most",
            drift_sentences(f) <= 2,
        ),
        (
            "Does not ask about PCI-DSS / card data (PCI has no guardrails for what this change touches)",
            not has(r"[^.\n]*(pci|card)[^.\n]*\?", f),
        ),
        (
            "Notifications queue retention set to 7 days (604800 s)",
            604800 in numbers_set("message_retention_seconds", q),
        ),
        (
            "The never-deployed queue gets a customer-managed key, or the reply says it doesn't",
            (has(r"kms_master_key_id\s*=", q) and not has(r"alias/aws/sqs", q))
            or said_together(f, KEY, KEY_COST + "|" + still, within=prose_lines),
        ),
        (
            "The never-deployed queue gets a redrive policy with maxReceiveCount <= 5, or the reply says it doesn't",
            (bool(r) and int(r.group(1)) <= 5)
            # a session may weigh the change's scope differently; what matters is that the reply
            # reports the gap it leaves
            or said_together(f, DLQ, DLQ_COST + "|" + still, within=prose_lines),
        ),
        (
            "If it gave the queue the repo's data-classification, the reply says so (the repo's default may be wrong for this data)",
            classification_said(d, "aws_sqs_queue.notifications", f),
        ),
        (
            "The never-deployed queue carries the required tags (literal or local.finops_tags)",
            has(r"local\.finops_tags|cost-center", q),
        ),
        (
            "Final response says the queue is new on this branch (never deployed), so fixing it costs nothing live",
            has(
                r"not (yet )?(been )?(deployed|merged|applied|live)|never (been )?(deployed|applied)"
                r"|(on|in) this branch|only exists on|isn'?t (deployed|live|on main)|not on main"
                r"|this branch (is what )?(adds|creates|introduces)",
                f,
            ),
        ),
        (
            "Changed nothing in the resources already on main: " + "; ".join(bad),
            not bad,
        ),
    ]
