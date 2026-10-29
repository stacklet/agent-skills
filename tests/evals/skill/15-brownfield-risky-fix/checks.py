from grading import (
    block,
    classification_said,
    drift_sentences,
    has,
    nocomments,
    numbers_set,
    prose_lines,
    said_together,
    unexpected_changes,
)


SHOWS = ("aws.rds",)


# said as the organisation's guardrail ("still flagged", "the platform", its severity): "scanners
# will flag it", "Security Hub reports a finding", "as a rule" or "any audit would flag it" is
# general advice, so a bare "flagged", "finding" or "rule" doesn't count
GUARDRAIL_SAID = (
    r"guardrail|polic|\bCIS\b|stacklet|\bplatform\b|still flagged|severity|"
    r"(?-i:\bHIGH\b|\bCRITICAL\b)|"
    r"\b(?:your|our|the)\s+(?:org(?:ani[sz]ation)?|company|team)(?:'s)?\s+(?:\w+\s+)?(?:rule|standard)s?\b"
)


def checks(d, t, f):
    db = nocomments(block("aws_db_instance", "orders", t))
    # apply_immediately makes the resize happen now rather than in the next maintenance window,
    # but it stays for later changes too (a restart would come at once): only if the reply says so
    allowed = r"^allocated_storage =" + (
        r"|^apply_immediately = true$" if has(r"apply_immediately", f) else ""
    )
    bad = unexpected_changes(
        d,
        {"aws_db_instance.orders": allowed},
        touched=("aws_db_instance.orders",),
    )
    return [
        (
            "If it gave the database the repo's data-classification, the reply says so (the repo's default may be wrong for this data)",
            classification_said(d, "aws_db_instance.orders", f),
        ),
        (
            "Mentions the unrelated docs bucket's drift in two sentences at most",
            drift_sentences(f) <= 2,
        ),
        ("allocated_storage = 200", 200 in numbers_set("allocated_storage", db)),
        (
            "Did not turn on storage_encrypted (that replaces the live database)",
            db and not has(r"storage_encrypted\s*=\s*true", db),
        ),
        (
            "Changed nothing else in existing resources (apply_immediately only if the reply says so): "
            + "; ".join(bad),
            not bad,
        ),
        (
            "Final response says the database is unencrypted against a guardrail, and that fixing it means replacing it (snapshot/restore, downtime)",
            said_together(
                f,
                r"encrypt",
                r"replac|snapshot|recreat|new instance|downtime|destroy|migrat",
                # as a guardrail's finding: noticing the gap is general knowledge (most runs
                # without the skill do), knowing the platform flags it is what the skill adds
                GUARDRAIL_SAID,
                within=prose_lines,
            ),
        ),
    ]
