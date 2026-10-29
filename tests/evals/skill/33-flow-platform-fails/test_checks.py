"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

import pytest

from controls import check, flow, run
from grading import cid


EVAL = "flow-platform-fails"
CIS, ACME = cid("CIS AWS Baseline"), cid("Acme Org Standards")
STATE = {"refresh_id": "r1", "memberships": {CIS: "r1"}, "accounts": []}


def fetched(*batches):
    return [{"kind": "collections", "exact": True, "collections": b} for b in batches]


def test_eval33_passes_one_fetch_cis_loaded_and_acme_said_missing(tmp_path):
    out = run(
        tmp_path,
        "Acme Org Standards couldn't be loaded, so its rules may still apply.",
        repo=False,
    )
    flow(out, fetched([CIS, ACME]), STATE)
    for starts in ("Asked for", "Ingested", "Tells the user"):
        assert check(EVAL, out, starts), starts


@pytest.mark.parametrize("batches", [[], [[CIS]], [[CIS, ACME], [ACME]]])
def test_eval33_fails_never_asking_or_retrying(tmp_path, batches):
    out = run(tmp_path, "Done.", repo=False)
    flow(out, fetched(*batches), STATE)
    assert not check(EVAL, out, "Asked for")


def test_eval33_fails_nothing_ingested(tmp_path):
    out = run(tmp_path, "Done.", repo=False)
    flow(out, fetched([CIS, ACME]), {**STATE, "memberships": {}})
    assert not check(EVAL, out, "Ingested")


def test_eval33_fails_saying_nothing_applies(tmp_path):
    out = run(tmp_path, "No Acme Org Standards rules apply to this bucket.", repo=False)
    flow(out, fetched([CIS, ACME]), STATE)
    assert not check(EVAL, out, "Tells the user")


def test_eval33_fails_finding_nothing_said_as_could_not(tmp_path):
    # "could not" about finding issues says the org standards were checked, the opposite
    out = run(tmp_path, "Acme Org Standards: I could not find any issues.", repo=False)
    flow(out, fetched([CIS, ACME]), STATE)
    assert not check(EVAL, out, "Tells the user")


def test_eval33_fails_no_bucket_and_no_public_access_block(tmp_path):
    out = run(tmp_path, "Acme Org Standards couldn't be loaded.", repo=False)
    flow(out, fetched([CIS, ACME]), STATE)
    assert not check(EVAL, out, "Wrote the bucket")
    assert not check(EVAL, out, "Applied what did load")


def test_eval33_fails_the_posture_late_and_a_query_it_didnt_write(tmp_path):
    out = run(tmp_path, "Done.", repo=False)
    log = [
        {"kind": "collections", "exact": False, "collections": [CIS, ACME]},
        {"kind": "posture", "exact": True},
    ]
    flow(out, log, STATE)
    assert not check(EVAL, out, "Fetched the posture before")
    assert not check(EVAL, out, "Sent only")
