from grading import (
    block,
    drift_sentences,
    has,
    nocomments,
    numbers_set,
    unexpected_changes,
)


SHOWS = ("aws.ebs",)


def checks(d, t, f):
    bad = unexpected_changes(
        d,
        {"aws_ebs_volume.batch_data": r"^size ="},
        touched=("aws_ebs_volume.batch_data",),
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
            "Volume size = 500",
            500
            in numbers_set(
                "size", nocomments(block("aws_ebs_volume", "batch_data", t))
            ),
        ),
        (
            "Only the size changed (gp2 -> gp3 lowers this volume's max throughput, so it is the user's call; no IMDSv2 or tags on the instance): "
            + "; ".join(bad),
            not bad,
        ),
    ]
