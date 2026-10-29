from grading import PCI_UUID, TAGS_OK, commands, has, scope_decisions


# PCI is in scope here (asked about, decided, or reached through an account)
PCI_IN_SCOPE = True


SHOWS = ("aws.rds",)


def checks(d, t, f):
    scope = d / ".stacklet" / "guardrails" / "scope.json"
    return [
        ("Required tags present", TAGS_OK(t)),
        (
            "Storage encrypted and not publicly accessible",
            has(r"storage_encrypted\s*=\s*true", t)
            and not has(r"publicly_accessible\s*=\s*true", t),
        ),
        (
            "Does not re-ask about PCI-DSS / card data (no question mentioning it)",
            not has(r"[^.\n]*(pci|card|payment)[^.\n]*\?", f),
        ),
        (
            "Scope file's decisions unchanged: PCI-DSS AWS still excluded, nothing else decided",
            scope_decisions(scope) == {PCI_UUID: "exclude"},
        ),
    ]


def process_checks(calls, arm):
    if arm == "without_skill":
        return []
    return [
        (
            "Left the recorded scope alone (no scope.py decide)",
            not commands(calls, r"scope\.py\s+\S+\s+decide"),
        )
    ]
