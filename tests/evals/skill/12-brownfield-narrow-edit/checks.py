from grading import (
    DLQ,
    GAP,
    KEY,
    block,
    claims,
    classification_said,
    drift_sentences,
    has,
    nocomments,
    numbers_set,
    said_together,
    unexpected_changes,
)


SHOWS = ("aws.sqs",)


def checks(d, t, f):
    timeout = numbers_set(
        "visibility_timeout_seconds",
        nocomments(block("aws_sqs_queue", "orders", t)),
    )
    bad = unexpected_changes(
        d,
        {
            "aws_sqs_queue.orders": r"^visibility_timeout_seconds =",
            # the request implies the function's timeout may need raising too
            "aws_lambda_function.orders_consumer": r"^timeout =",
        },
        touched=("aws_sqs_queue.orders", "aws_lambda_function.orders_consumer"),
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
            "Orders queue visibility_timeout_seconds raised to >= 90",
            bool(timeout) and min(timeout) >= 90,
        ),
        (
            "Changed nothing else (no DLQ or CMK added, no new resources; repo-valued tags allowed): "
            + "; ".join(bad),
            not bad,
        ),
        (
            "If it gave the queue the repo's data-classification, the reply says so (the repo's default may be wrong for this data)",
            classification_said(d, "aws_sqs_queue.orders", f),
        ),
        (
            "Final response tells the user the queue already lacks a DLQ/redrive policy and a customer-managed key",
            said_together(f, DLQ, GAP, within=claims)
            and said_together(f, KEY + r"|customer[- ]managed", GAP, within=claims),
        ),
    ]
