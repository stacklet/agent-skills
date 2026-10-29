"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import bash, check, flow, run, transcript, write
from grading import cid


EVAL = "flow-first-use"
CIS, ACME = cid("CIS AWS Baseline"), cid("Acme Org Standards")
BUCKET = 'resource "aws_s3_bucket" "artifacts" {}'
GOOD_LOG = [
    {"kind": "deployment", "exact": True},
    {"kind": "posture", "exact": True},
    {"kind": "collections", "exact": True, "collections": [CIS, ACME]},
]
LOADED = {"refresh_id": "r1", "memberships": {CIS: "r1", ACME: "r1"}, "accounts": []}


def graded(tmp_path, log=GOOD_LOG, state=LOADED, calls=None):
    out = run(tmp_path, "Done.", repo=False)
    flow(out, log, state)
    transcript(
        out,
        *(
            calls
            or (
                bash("python3 /s/scripts/ingest.py d posture /t/p.json", True),
                bash("python3 /s/scripts/ingest.py d collections /t/c.json", True),
                bash("python3 /s/scripts/distill.py d show aws.s3"),
                write("/w/outputs/main.tf", BUCKET),
            )
        ),
    )
    return out


def test_eval29_passes_a_sync_then_the_bucket(tmp_path):
    out = graded(tmp_path)
    for starts in (
        "Fetched the posture, once",
        "Fetched the posture before",
        "Fetched no",
        "Sent only",
        "Ingested",
        "Wrote the bucket",
    ):
        assert check(EVAL, out, starts), starts


def test_eval29_fails_a_refetch_a_rewritten_query_and_the_posture_late(tmp_path):
    log = [
        {"kind": "collections", "exact": False, "collections": [CIS]},
        {"kind": "posture", "exact": True},
        {"kind": "collections", "exact": True, "collections": [CIS, ACME]},
    ]
    out = graded(tmp_path, log=log)
    assert not check(EVAL, out, "Fetched the posture before")
    assert not check(EVAL, out, "Fetched no")
    assert not check(EVAL, out, "Sent only")


def test_eval29_fails_what_was_fetched_but_not_ingested(tmp_path):
    # a stale membership, from an earlier posture: the fetch didn't land
    out = graded(tmp_path, state={**LOADED, "memberships": {CIS: "r0", ACME: "r1"}})
    assert not check(EVAL, out, "Ingested")


def test_eval29_fails_the_bucket_written_before_the_sync(tmp_path):
    out = graded(
        tmp_path,
        calls=(
            write("/w/outputs/main.tf", BUCKET),
            bash("python3 /s/scripts/ingest.py d collections /t/c.json", True),
        ),
    )
    assert not check(EVAL, out, "Wrote the bucket")


def test_eval29_fails_the_bucket_written_through_the_shell_before_the_sync(tmp_path):
    out = graded(
        tmp_path,
        calls=(
            bash(f"cat > main.tf <<'EOF'\n{BUCKET}\nEOF"),
            bash("python3 /s/scripts/ingest.py d collections /t/c.json", True),
        ),
    )
    assert not check(EVAL, out, "Wrote the bucket")


def test_eval29_passes_the_bucket_written_through_the_shell_after_the_sync(tmp_path):
    out = graded(
        tmp_path,
        calls=(
            bash("python3 /s/scripts/ingest.py d collections /t/c.json", True),
            bash(f"cat > main.tf <<'EOF'\n{BUCKET}\nEOF"),
        ),
    )
    assert check(EVAL, out, "Wrote the bucket")


def test_eval29_fails_the_posture_fetched_twice(tmp_path):
    out = graded(tmp_path, log=[GOOD_LOG[0], GOOD_LOG[1], GOOD_LOG[1], GOOD_LOG[2]])
    assert not check(EVAL, out, "Fetched the posture, once")
