"""Controls for this eval's checks: the scope file must record the decision, not merely contain
the words."""

import json

from controls import check, run
from grading import PCI_UUID


def scope(decision):
    return json.dumps(
        {
            "version": 5,
            "decisions": [
                {
                    "collection": PCI_UUID,
                    "name": "PCI-DSS AWS",
                    "decision": decision,
                    "because": "include it? the user said no",
                }
            ],
        }
    )


SCOPE = ".stacklet/guardrails/scope.json"


def test_eval2_fails_a_scope_file_that_excludes_pci(tmp_path):
    out = run(tmp_path, "done", {SCOPE: scope("exclude")}, repo=False)
    assert not check("rds-payments-candidate", out, "Recorded scope bringing in")


def test_eval2_passes_a_scope_file_that_includes_pci(tmp_path):
    out = run(tmp_path, "done", {SCOPE: scope("include")}, repo=False)
    assert check("rds-payments-candidate", out, "Recorded scope bringing in")


def answered(answer, decision=None):
    state = {
        "version": 5,
        "answers": [
            {
                "question": "aws-payments-team",
                "name": "Does this code deploy into an AWS account run by the payments team?",
                "answer": answer,
            }
        ],
    }
    if decision:
        state["decisions"] = [{"collection": PCI_UUID, "decision": decision}]
    return json.dumps(state)


def test_eval2_passes_yes_to_the_payments_question(tmp_path):
    # how the posture assessment brings PCI in: no include needed
    out = run(tmp_path, "done", {SCOPE: answered("yes")}, repo=False)
    assert check("rds-payments-candidate", out, "Recorded scope bringing in")


def test_eval2_fails_no_to_it_or_an_exclude_over_a_yes(tmp_path):
    out = run(tmp_path, "done", {SCOPE: answered("no")}, repo=False)
    assert not check("rds-payments-candidate", out, "Recorded scope bringing in")
    (tmp_path / "x").mkdir()
    out = run(tmp_path / "x", "done", {SCOPE: answered("yes", "exclude")}, repo=False)
    assert not check("rds-payments-candidate", out, "Recorded scope bringing in")
