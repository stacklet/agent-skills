from grading import (
    TAGS_OK,
    all_exact,
    before,
    has,
    loaded,
    names,
    recorded_account_check,
    recorded_accounts,
    refetched,
    said_together,
    written_after,
)


SHOWS = ("aws.ec2",)
# the sandbox's Owner tag is in scope here: only PCI is noise
OUT_OF_SCOPE = r"\bPCI\b|card ?data|cardholder"


def checks(d, t, f):
    scope = d / ".stacklet" / "guardrails" / "scope.json"
    return [
        (
            "Recorded the provider's account in .stacklet/guardrails/scope.json",
            "222222222222" in (recorded_accounts(scope) or set()),
        ),
        (
            "Tags the instance with Owner (Sandbox Tagging, in scope through the account)",
            has(r"""["']?Owner["']?\s*=""", t),
        ),
        (
            "Says an instance without an Owner tag is marked and stopped",
            said_together(f, r"\bOwner\b", r"\bstop"),
        ),
        ("Tags cost-center and data-classification with a valid value", TAGS_OK(t)),
    ]


def process_checks(calls, arm):
    return recorded_account_check(calls, arm)


def server_checks(log, state, calls):
    return [
        (
            "Looked the account up before fetching what it brings in (Sandbox Tagging)",
            before(
                log,
                lambda x: x.get("account") == "AWS:222222222222",
                names("Sandbox Tagging"),
            ),
        ),
        (
            "Ingested the account and Sandbox Tagging",
            "AWS:222222222222" in state.get("accounts", [])
            and loaded(state, "Sandbox Tagging"),
        ),
        (
            "Wrote the instance only once Sandbox Tagging, which the lookup brings in, was ingested",
            written_after(
                calls, "collections", r'resource\s+"aws_instance"', since="accounts"
            ),
        ),
        ("Fetched no collection twice", not refetched(log)),
        ("Sent only the skill's own queries", all_exact(log)),
        (
            "Never fetched the posture (the cache's is current)",
            not any(x.get("kind") == "posture" for x in log),
        ),
    ]
