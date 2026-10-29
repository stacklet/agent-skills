"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import bash, check, flow, run, transcript, write
from grading import cid


EVAL = "flow-day-old-cache"
CIS, ACME = cid("CIS AWS Baseline"), cid("Acme Org Standards")


def graded(tmp_path, log, memberships):
    out = run(tmp_path, "Done.", repo=False)
    flow(out, log, {"refresh_id": "new", "memberships": memberships, "accounts": []})
    return out


def test_eval30_passes_a_refresh_then_the_collections_checked(tmp_path):
    log = [
        {"kind": "posture", "exact": True},
        {"kind": "collections", "exact": True, "collections": [CIS, ACME]},
    ]
    out = graded(tmp_path, log, {CIS: "new", ACME: "new"})
    for starts in (
        "Refreshed the posture, once",
        "Refreshed the posture before",
        "Fetched no",
        "Sent only",
        "Checked",
    ):
        assert check(EVAL, out, starts), starts


def test_eval30_fails_no_refresh_and_memberships_left_old(tmp_path):
    # the old cache read as it was: nothing refreshed, nothing checked
    out = graded(tmp_path, [], {CIS: "old", ACME: "old"})
    assert not check(EVAL, out, "Refreshed the posture, once")
    assert not check(EVAL, out, "Refreshed the posture before")
    assert not check(EVAL, out, "Checked")


def test_eval30_fails_a_refetch_and_a_query_it_didnt_write(tmp_path):
    log = [
        {"kind": "posture", "exact": True},
        {"kind": "collections", "exact": False, "collections": [CIS, ACME]},
        {"kind": "collections", "exact": True, "collections": [CIS]},
    ]
    out = graded(tmp_path, log, {CIS: "new", ACME: "new"})
    assert not check(EVAL, out, "Fetched no")
    assert not check(EVAL, out, "Sent only")


SHOW = bash("python3 /s/scripts/distill.py d show aws.sqs")
INGEST = bash("python3 /s/scripts/ingest.py d collections /t/c.json", True)


def test_eval30_passes_the_old_guidance_then_a_second_look(tmp_path):
    # written to the old posture's guidance while the sync ran, then checked again
    out = graded(tmp_path, [], {})
    transcript(
        out,
        SHOW,
        write("/w/outputs/main.tf", 'resource "aws_sqs_queue" "q" {}'),
        INGEST,
        SHOW,
    )
    assert check(EVAL, out, "Looked aws.sqs up again")


def test_eval30_fails_no_look_after_the_sync(tmp_path):
    out = graded(tmp_path, [], {})
    transcript(out, SHOW, INGEST)
    assert not check(EVAL, out, "Looked aws.sqs up again")
