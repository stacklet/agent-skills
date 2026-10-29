"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import check, flow, run


EVAL = "flow-question-for-another-type"
EMPTY = {"refresh_id": "r1", "memberships": {}, "accounts": []}


def test_eval32_fails_asking_about_payments(tmp_path):
    out = run(tmp_path, "Will this run in one of your payments accounts?", repo=False)
    flow(out, [], EMPTY)
    assert not check(EVAL, out, "Does not ask")


def test_eval32_passes_mentioning_payments_without_asking(tmp_path):
    out = run(tmp_path, "The worker runs the payment reconciliation jobs.", repo=False)
    flow(out, [], EMPTY)
    assert check(EVAL, out, "Does not ask")
    assert check(EVAL, out, "Fetched nothing")


def test_eval32_fails_a_sync_it_didnt_need(tmp_path):
    out = run(tmp_path, "Done.", repo=False)
    flow(out, [{"kind": "posture", "exact": True}], EMPTY)
    assert not check(EVAL, out, "Fetched nothing")
