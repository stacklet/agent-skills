from grading import TRIAL_REMEDIATION, has, recorded_account_check, recorded_accounts


SHOWS = ("gcp.bucket",)
OUT_OF_SCOPE = TRIAL_REMEDIATION + r"|GuardrailPause|valkey|graviton"


def checks(d, t, f):
    scope = d / ".stacklet" / "guardrails" / "scope.json"
    return [
        (
            "Recorded the provider's project in .stacklet/guardrails/scope.json",
            "atelier-render-prod" in (recorded_accounts(scope) or set()),
        ),
        (
            "Labels atelier-team and cost-code",
            has(r'"?atelier-team"?\s*=', t) and has(r'"?cost-code"?\s*=', t),
        ),
        ("Versioning enabled", has(r"versioning\s*\{[^}]*enabled\s*=\s*true", t)),
        (
            "Uniform bucket-level access",
            has(r"uniform_bucket_level_access\s*=\s*true", t),
        ),
        (
            "Does not ask which org (the project answered it)",
            not has(r"[^.\n]*(atelier\.example|which org|organi[sz]ation)[^.\n]*\?", f),
        ),
    ]


def process_checks(calls, arm):
    return recorded_account_check(calls, arm, what="project")
