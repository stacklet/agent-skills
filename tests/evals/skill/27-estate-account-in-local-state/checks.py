from grading import (
    FIXTURES,
    OUT_OF_SCOPE_TRIAL,
    OWNER,
    default_tags,
    mrd_tags_ok,
    new_blocks,
    owner,
    recorded_account_check,
    recorded_accounts,
    said_together,
    shared_tags,
    tags_of,
)


SHOWS = ("aws.sqs",)
OUT_OF_SCOPE = OUT_OF_SCOPE_TRIAL


def checks(d, t, f):
    scope = d / ".stacklet" / "guardrails" / "scope.json"
    # the fixture's own queue is tagged already: only the queues the agent added count
    queues = new_blocks("aws_sqs_queue", t, FIXTURES / "estate-local-state-repo")
    return [
        (
            "Recorded the local state's account in .stacklet/guardrails/scope.json",
            "456789012345" in (recorded_accounts(scope) or set()),
        ),
        (
            "Every new queue has an Owner tag (directly or through a shared tags value)",
            bool(queues)
            and all(
                owner(b) or owner(shared_tags(b, t)) or owner(default_tags(t))
                for b in queues.values()
            ),
        ),
        (
            "Warns that resources without an Owner tag are deleted",
            said_together(f, OWNER, r"delete|deleting|deletion"),
        ),
        (
            "Tags new queues mrd:owner, mrd:service, mrd:environment (dev/stage/prod)",
            bool(queues)
            and all(mrd_tags_ok(tags_of(b, t), t) for b in queues.values()),
        ),
    ]


def process_checks(calls, arm):
    return recorded_account_check(calls, arm)
