import re

from grading import (
    all_exact,
    before,
    has,
    is_kind,
    loaded,
    said_together,
    times_fetched,
)


SHOWS = ("aws.s3",)


def checks(d, t, f):
    return [
        ("Wrote the bucket", has(r"aws_s3_bucket\b", t)),
        (
            "Applied what did load (CIS: all four public access block flags)",
            len(
                re.findall(
                    r"(block_public_acls|block_public_policy|ignore_public_acls|restrict_public_buckets)\s*=\s*true",
                    t,
                )
            )
            >= 4,
        ),
        (
            "Tells the user the org standards' guardrails are missing, not absent",
            said_together(
                f,
                r"Acme|org(ani[sz]ation(al)?)? standards",
                # that its guardrails couldn't be had, not that it found nothing ("could not
                # find any issues" says the opposite)
                r"missing|not (be )?(loaded|fetched|available|checked)"
                r"|could(n.t| not) (be )?(load|fetch|check|get|retrieve|serve)"
                r"|failed to|unavailable|incomplete|(returned|gave|an) error"
                r"|error (fetching|loading)",
            ),
        ),
    ]


def server_checks(log, state, calls):
    return [
        (
            "Asked for the collection the platform failed exactly once (no retry: it's reported)",
            times_fetched(log, "Acme Org Standards") == 1,
        ),
        (
            "Fetched the posture before any collection",
            before(log, is_kind("posture"), is_kind("collections")),
        ),
        ("Sent only the skill's own queries", all_exact(log)),
        (
            "Ingested what the platform did serve (CIS), and not the failed one",
            loaded(state, "CIS AWS Baseline")
            and not loaded(state, "Acme Org Standards"),
        ),
    ]
