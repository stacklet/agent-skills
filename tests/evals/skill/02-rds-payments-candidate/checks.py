from grading import PCI_UUID, has, numbers_set, scope_answers, scope_decisions


# PCI is in scope here (asked about, decided, or reached through an account)
PCI_IN_SCOPE = True


SHOWS = ("aws.rds",)


def checks(d, t, f):
    scope = d / ".stacklet" / "guardrails" / "scope.json"
    retention = numbers_set("backup_retention_period", t)
    return [
        (
            "backup_retention_period >= 35, literal or variable default (PCI-DSS brought into scope)",
            bool(retention) and min(retention) >= 35,
        ),
        ("storage_encrypted = true", has(r"storage_encrypted\s*=\s*true", t)),
        ("Not publicly accessible", not has(r"publicly_accessible\s*=\s*true", t)),
        ("multi_az = true as requested", has(r"multi_az\s*=\s*true", t)),
        (
            "Recorded scope bringing in PCI-DSS AWS in .stacklet/guardrails/scope.json",
            pci_recorded(scope),
        ),
    ]


def pci_recorded(scope):
    """The scope file brings PCI-DSS AWS in: an include, or (the posture assessment's way) yes to a
    question about payments or card data, matched by its text since ids are the model's. An
    exclude wins, as it does in scope.py."""
    decision = (scope_decisions(scope) or {}).get(PCI_UUID)
    if decision:
        return decision == "include"
    return any(
        a == "yes" and has(r"payment|card|PCI", q)
        for q, a in scope_answers(scope) or []
    )
