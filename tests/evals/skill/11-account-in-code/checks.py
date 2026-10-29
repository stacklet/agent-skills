from grading import (
    TAGS_OK,
    has,
    numbers_set,
    recorded_account_check,
    recorded_accounts,
)


# PCI is in scope here (asked about, decided, or reached through an account)
PCI_IN_SCOPE = True


SHOWS = ("aws.rds",)


def checks(d, t, f):
    scope = d / ".stacklet" / "guardrails" / "scope.json"
    retention = numbers_set("backup_retention_period", t)
    return [
        (
            "Recorded the provider's account in .stacklet/guardrails/scope.json",
            "111111111111" in (recorded_accounts(scope) or set()),
        ),
        (
            "backup_retention_period >= 35, literal or variable default (PCI-DSS applies through the account)",
            bool(retention) and min(retention) >= 35,
        ),
        (
            "Does not ask about PCI-DSS / card data (the account answered it)",
            not has(r"[^.\n]*(pci|card|payment)[^.\n]*\?", f),
        ),
        ("storage_encrypted = true", has(r"storage_encrypted\s*=\s*true", t)),
        ("Not publicly accessible", not has(r"publicly_accessible\s*=\s*true", t)),
        # the fixture's ORG rules
        ("Tags cost-center and data-classification with a valid value", TAGS_OK(t)),
    ]


def process_checks(calls, arm):
    return recorded_account_check(calls, arm)
