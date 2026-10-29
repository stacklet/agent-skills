from grading import (
    block,
    drift_sentences,
    has,
    nocomments,
    numbers_set,
    unexpected_changes,
)


SHOWS = ("aws.lambda",)


def checks(d, t, f):
    fn = nocomments(block("aws_lambda_function", "orders_consumer", t))
    bad = unexpected_changes(
        d,
        {"aws_lambda_function.orders_consumer": r"^memory_size ="},
        touched=("aws_lambda_function.orders_consumer",),
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
        ("Lambda memory_size = 1024", 1024 in numbers_set("memory_size", fn)),
        (
            (
                "Did not add reserved_concurrent_executions (a guessed cap can throttle the live consumer) "
                "or tracing_config (needs X-Ray permissions on a role this repo doesn't manage)"
            ),
            fn and not has(r"reserved_concurrent_executions|tracing_config", fn),
        ),
        ("Changed nothing else in existing resources: " + "; ".join(bad), not bad),
        (
            "Final response tells the user the function lacks reserved concurrency and X-Ray tracing",
            has(r"reserved[_ ]concurren|reserve\w*\W+(\w+\W+)?concurren", f)
            and has(r"x-?ray|tracing", f),
        ),
    ]
