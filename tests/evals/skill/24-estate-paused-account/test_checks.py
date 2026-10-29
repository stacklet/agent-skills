"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

import json

import pytest

from controls import check, run


def scope(answers):
    return json.dumps(
        {
            "version": 5,
            "deployment": "console.estate.example",
            "accounts": [
                {"provider": "AWS", "key": "345678901234", "because": "providers.tf"}
            ],
            "answers": [
                {"question": q, "name": n, "answer": a, "because": "lookup"}
                for q, n, a in answers
            ],
        }
    )


PAUSED = (
    "aws-pause-only",
    "Does this code deploy ONLY into accounts tagged GuardrailPause?",
    "yes",
)
SCOPE = ".stacklet/guardrails/scope.json"


def test_eval24_passes_answering_from_the_lookup_and_saying_so(tmp_path):
    reply = (
        "Account 345678901234 carries the GuardrailPause tag, so the wave guardrails (Valkey, "
        "the mrd: tags) don't apply to it; only the org-wide ones do."
    )
    out = run(tmp_path, reply, {SCOPE: scope([PAUSED])}, repo=False)
    for starts in ("Recorded the provider", "Recorded yes", "Says the paused"):
        assert check("estate-paused-account", out, starts), starts


def test_eval24_fails_presenting_the_wave_guardrails_as_applying(tmp_path):
    reply = "Used Valkey and the mrd: tags, as the guardrails require."
    out = run(tmp_path, reply, {SCOPE: scope([])}, repo=False)
    assert check("estate-paused-account", out, "Recorded the provider")
    assert not check("estate-paused-account", out, "Recorded yes")
    assert not check("estate-paused-account", out, "Says the paused")


def test_eval24_fails_a_no_to_the_opt_out(tmp_path):
    no = (*PAUSED[:2], "no")
    out = run(tmp_path, "Done.", {SCOPE: scope([no])}, repo=False)
    assert not check("estate-paused-account", out, "Recorded yes")


def test_eval24_passes_saying_the_pause_turns_them_off(tmp_path):
    # as real runs put it
    for i, reply in enumerate(
        (
            (
                "Your target account (345678901234) is tagged `GuardrailPause=until-2027-01`. That "
                "means none of the ElastiCache policies apply to it right now."
            ),
            (
                "That account is tagged `GuardrailPause=until-2027-01`, so the three ElastiCache "
                "guardrails (Valkey engine, encryption, required tags) don't currently apply to it."
            ),
            (
                "Account 345678901234 is tagged GuardrailPause=until-2027-01. That pause turns "
                "off every collection with ElastiCache policies."
            ),
            (
                "That turns off every ElastiCache guardrail for this account for now, since it "
                "is tagged GuardrailPause."
            ),
            # the tag in one sentence, what it does in the next
            (
                "- **This won't last.** Your deploy role points at account 345678901234, which is "
                "tagged `GuardrailPause=until-2027-01`. That tag switches off nine policy groups."
            ),
            (
                "- **Paused account:** 345678901234 is tagged `GuardrailPause=until-2027-01`. "
                "That turns off nine collections of guardrails for this repo."
            ),
            # what "none of" covers named in code
            (
                "- **Paused for now:** the target account is `analytics-03` (345678901234). It's "
                "tagged `GuardrailPause=until-2027-01`, so for now none of your organisation's "
                "`mrd-prod-*` policies apply to it."
            ),
        )
    ):
        (tmp_path / str(i)).mkdir()
        out = run(tmp_path / str(i), reply, {SCOPE: scope([PAUSED])}, repo=False)
        assert check("estate-paused-account", out, "Says the paused"), reply


def test_eval24_takes_the_opt_out_however_the_question_words_it(tmp_path):
    # the assessing model words the question; a yes to it is the same answer
    worded = (
        "aws-optout-only",
        "Does this code deploy only into opted-out accounts?",
        "yes",
    )
    out = run(tmp_path, "Done.", {SCOPE: scope([worded])}, repo=False)
    assert check("estate-paused-account", out, "Recorded yes")


def test_eval24_fails_the_pause_beside_an_unrelated_claim(tmp_path):
    reply = (
        "The account is tagged GuardrailPause=until-2027-01. I did not add an AUTH token.\n\n"
        "Encryption in transit is on, so the guardrails don't apply any extra checks here."
    )
    out = run(tmp_path, reply, {SCOPE: scope([PAUSED])}, repo=False)
    assert not check("estate-paused-account", out, "Says the paused")


def test_eval24_fails_one_word_read_as_both_the_opt_out_and_what_it_does(tmp_path):
    # "exempt" names neither the tag nor the opt-out: it is only what the opt-out would do
    reply = "Used Valkey and the mrd: tags: this account is exempt from nothing else."
    out = run(tmp_path, reply, {SCOPE: scope([PAUSED])}, repo=False)
    assert not check("estate-paused-account", out, "Says the paused")


@pytest.mark.parametrize(
    "reply",
    [
        (
            "The account is tagged GuardrailPause, but nothing is excluded: Valkey and the mrd: "
            "tags apply as everywhere else."
        ),
        (
            "The account is tagged GuardrailPause; it is not excluded, so the wave guardrails "
            "(Valkey, the mrd: tags) apply."
        ),
        "The account is tagged GuardrailPause, which isn't exempt from the wave guardrails.",
    ],
)
def test_eval24_fails_a_negated_exclusion(tmp_path, reply):
    out = run(tmp_path, reply, {SCOPE: scope([PAUSED])}, repo=False)
    assert not check("estate-paused-account", out, "Says the paused")


def test_eval24_passes_the_opt_out_saying_it_is_excluded(tmp_path):
    reply = (
        "Account 345678901234 is tagged GuardrailPause, so it is excluded from every wave "
        "group: Valkey and the mrd: tags aren't required there."
    )
    out = run(tmp_path, reply, {SCOPE: scope([PAUSED])}, repo=False)
    assert check("estate-paused-account", out, "Says the paused")
