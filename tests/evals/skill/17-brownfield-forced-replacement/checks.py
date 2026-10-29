import re

from grading import (
    KEY,
    KEY_COST,
    blocks,
    drift_sentences,
    has,
    nocomments,
    prose_lines,
    said_together,
    unexpected_changes,
)


SHOWS = ("aws.sqs",)


def checks(d, t, f):
    tc = nocomments(t)
    fifo = [
        b
        for b in blocks("aws_sqs_queue", tc).values()
        if has(r"fifo_queue\s*=\s*true", b) and not has(r"redrive_allow_policy", b)
    ]
    main = [b for b in fifo if has(r"redrive_policy", b)] or fifo
    q = main[0] if main else ""
    r = re.search(r"maxReceiveCount\W+(\d+)", q) or re.search(
        r"max_receive_count\W+(\d+)", q
    )
    bad = unexpected_changes(
        d,
        new=r"aws_sqs_\w+|aws_kms_\w+|aws_lambda_event_source_mapping|aws_iam_\w+",
        allowed={
            # the queue is replaced whatever happens; its mapping may follow it
            "aws_sqs_queue.orders": r".",
            "aws_lambda_event_source_mapping.orders": r".",
        },
        touched=("aws_sqs_queue.orders",),
        may_remove=("aws_sqs_queue.orders",),
    )
    fifo_labels = {
        label
        for label, b in blocks("aws_sqs_queue", tc).items()
        if has(r"fifo_queue\s*=\s*true", b)
    }
    consumer_reads_fifo = any(
        has(r"aws_lambda_function\.orders_consumer", m)
        and any(
            has(r"event_source_arn\s*=\s*aws_sqs_queue\." + label + r"\.arn", m)
            for label in fifo_labels
        )
        for m in blocks("aws_lambda_event_source_mapping", tc).values()
    )
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
            "The orders queue is FIFO (fifo_queue = true, name ending .fifo)",
            bool(q) and has(r"\.fifo", q),
        ),
        (
            "The consumer Lambda still reads from a FIFO queue (an event source mapping points at one)",
            consumer_reads_fifo,
        ),
        (
            (
                "The new queue uses a customer-managed key, or the reply explains why not (producers and the "
                "consumer role, managed elsewhere, would need kms:Decrypt / GenerateDataKey on it)"
            ),
            (has(r"kms_master_key_id\s*=", q) and not has(r"alias/aws/sqs", q))
            or said_together(f, KEY, KEY_COST, within=prose_lines),
        ),
        (
            "The new queue has a redrive policy with maxReceiveCount <= 5",
            bool(r) and int(r.group(1)) <= 5,
        ),
        (
            "The new queue carries the required tags (literal or local.finops_tags)",
            has(r"local\.finops_tags", q)
            or (has(r"cost-center", q) and has(r"data-classification", q)),
        ),
        (
            "Final response says the change replaces the queue",
            said_together(
                f, r"queue", r"replac|recreat|destroy|new queue|in[- ]flight|lost"
            ),
        ),
        ("Changed nothing else in existing resources: " + "; ".join(bad), not bad),
    ]
