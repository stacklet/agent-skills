from grading import (
    OUT_OF_SCOPE_TRIAL,
    OWNER,
    blocks,
    every_queue_owned,
    mrd_tags_ok,
    recorded_account_check,
    recorded_accounts,
    said_together,
    tags_of,
)


SHOWS = ("aws.sqs",)
OUT_OF_SCOPE = OUT_OF_SCOPE_TRIAL


def checks(d, t, f):
    queues = blocks("aws_sqs_queue", t)
    scope = d / ".stacklet" / "guardrails" / "scope.json"
    return [
        (
            "Recorded the provider's account in .stacklet/guardrails/scope.json",
            "456789012345" in (recorded_accounts(scope) or set()),
        ),
        (
            "Every queue has an Owner tag (directly or through a shared tags value)",
            every_queue_owned(t),
        ),
        (
            "Warns that resources without an Owner tag are deleted",
            said_together(f, OWNER, r"delete|deleting|deletion"),
        ),
        (
            # the queues' own tags: another resource's don't count
            "Tags every queue mrd:owner, mrd:service, mrd:environment (dev/stage/prod)",
            bool(queues)
            and all(mrd_tags_ok(tags_of(b, t), t) for b in queues.values()),
        ),
    ]


def process_checks(calls, arm):
    return recorded_account_check(calls, arm)
