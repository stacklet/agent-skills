from grading import (
    OPT_OUT,
    OPT_OUT_ASKED,
    OUT_OF_SCOPE_WAVES,
    has,
    prose_lines,
    recorded_account_check,
    recorded_accounts,
    scope_answers,
    unnegated,
)


OUT_OF_SCOPE = OUT_OF_SCOPE_WAVES
# What the opt-out does to the wave-rolled guardrails. No term of OPT_OUT's, so the claim takes two
# matches (the opt-out, and what it does), never one word read as both.
# A word or two may sit between ("don't currently apply"), and "none of" may name what it covers
# ("none of the ElastiCache policies apply", "none of your organisation's `mrd-prod-*` policies
# apply").
NOT_REACHED = (
    r"(don.?t|doesn.?t|do not|does not|won.?t|no longer|not)(\s+\w+){0,2}?\s+"
    r"(apply|reach|cover|bind)|"
    r"none of the wave|turns? off|turned off|switch(es|ed)? off|none( of [^.;\n]{0,60}?)? apply"
)
# ... and words that say it only when nothing negates them ("nothing is excluded" says the opposite)
NOT_REACHED_WORD = r"\b(?:exempt\w*|excluded|outside)\b"


def opt_out_claim(x):
    """X names the opt-out and says it takes the wave-rolled guardrails away."""
    return has(OPT_OUT, x) and (has(NOT_REACHED, x) or unnegated(NOT_REACHED_WORD, x))


def checks(d, t, f):
    scope = d / ".stacklet" / "guardrails" / "scope.json"
    answers = scope_answers(scope) or []
    return [
        (
            "Recorded the provider's account in .stacklet/guardrails/scope.json",
            "345678901234" in (recorded_accounts(scope) or set()),
        ),
        (
            "Recorded yes to the GuardrailPause opt-out question (by its text)",
            any(has(OPT_OUT_ASKED, q) and a == "yes" for q, a in answers),
        ),
        (
            "Says the paused account is outside the wave-rolled guardrails",
            # in one paragraph or list item: "It is tagged GuardrailPause. That tag switches
            # off nine collections" is one claim in two sentences
            any(opt_out_claim(x) for x in prose_lines(f)),
        ),
    ]


def process_checks(calls, arm):
    return recorded_account_check(calls, arm)
