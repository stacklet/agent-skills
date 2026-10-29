from grading import (
    OUT_OF_SCOPE_TRIAL,
    blocks,
    mrd_tags_ok,
    recorded_account_check,
    recorded_accounts,
    tags_of,
)


SHOWS = ("aws.sqs",)
OUT_OF_SCOPE = OUT_OF_SCOPE_TRIAL


def checks(d, t, f):
    queues = blocks("aws_sqs_queue", t)
    keys = recorded_accounts(d / ".stacklet" / "guardrails" / "scope.json")
    return [
        (
            "Recorded the account the provider's role is assumed into (210987654321)",
            bool(keys) and "210987654321" in keys,
        ),
        (
            "Did not record the local profile's account (456789012345): it only supplies credentials",
            bool(keys) and "456789012345" not in keys,
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
