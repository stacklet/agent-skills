"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import bash, check, flow, run, transcript, write
from grading import cid


EVAL = "flow-account-mid-task"
SANDBOX = cid("Sandbox Tagging")
INSTANCE = 'resource "aws_instance" "batch" {\n  tags = { Owner = "jdoe" }\n}'
LOOKUP = {"kind": "account", "exact": True, "account": "AWS:222222222222"}
FETCH = {"kind": "collections", "exact": True, "collections": [SANDBOX]}
STATE = {
    "refresh_id": "r1",
    "memberships": {SANDBOX: "r1"},
    "accounts": ["AWS:222222222222"],
}


def graded(tmp_path, log, calls, state=STATE):
    out = run(
        tmp_path, "Instances without an Owner tag are marked, then stopped.", repo=False
    )
    flow(out, log, state)
    transcript(out, *calls)
    return out


WAITED = (
    bash("python3 /s/scripts/ingest.py d accounts /t/account_1.json", True),
    bash("python3 /s/scripts/ingest.py d collections /t/c.json", True),
    write("/w/outputs/main.tf", INSTANCE),
)


def test_eval31_passes_lookup_fetch_then_the_instance(tmp_path):
    out = graded(tmp_path, [LOOKUP, FETCH], WAITED)
    for starts in (
        "Looked the account up",
        "Ingested",
        "Wrote the instance",
        "Never fetched",
    ):
        assert check(EVAL, out, starts), starts


def test_eval31_fails_a_fetch_the_lookup_didnt_bring_in(tmp_path):
    # the collection fetched first, as part of everything, rather than because of the account
    out = graded(tmp_path, [FETCH, LOOKUP], WAITED)
    assert not check(EVAL, out, "Looked the account up")


def test_eval31_fails_the_instance_written_before_the_lookup(tmp_path):
    # what the first batch's runs did: written, then corrected once the sync reported
    out = graded(
        tmp_path, [LOOKUP, FETCH], (write("/w/outputs/main.tf", INSTANCE), *WAITED)
    )
    assert not check(EVAL, out, "Wrote the instance")


def test_eval31_fails_a_lookup_that_was_never_ingested(tmp_path):
    out = graded(tmp_path, [LOOKUP, FETCH], WAITED, state={**STATE, "accounts": []})
    assert not check(EVAL, out, "Ingested")


def test_eval31_fails_the_instance_written_between_the_lookup_and_the_collection(
    tmp_path,
):
    # a run that did this passed when the check read the accounts ingest: the guardrail it needs
    # comes with the collection the lookup brings in
    calls = (
        bash("python3 /s/scripts/ingest.py d accounts /t/account_1.json", True),
        write("/w/outputs/main.tf", INSTANCE),
        bash("python3 /s/scripts/ingest.py d collections /t/c.json", True),
    )
    out = graded(tmp_path, [LOOKUP, FETCH], calls)
    assert not check(EVAL, out, "Wrote the instance")


def test_eval31_fails_a_collection_fetched_before_the_lookup_only(tmp_path):
    # a collections ingest before the lookup (the questions' collections) doesn't count
    calls = (
        bash("python3 /s/scripts/ingest.py d collections /t/q.json", True),
        bash("python3 /s/scripts/ingest.py d accounts /t/account_1.json", True),
        write("/w/outputs/main.tf", INSTANCE),
    )
    out = graded(tmp_path, [LOOKUP, FETCH], calls)
    assert not check(EVAL, out, "Wrote the instance")


def test_eval31_fails_a_posture_fetch_a_refetch_and_a_query_it_didnt_write(tmp_path):
    log = [{"kind": "posture", "exact": True}, LOOKUP, FETCH, {**FETCH, "exact": False}]
    out = graded(tmp_path, log, WAITED)
    assert not check(EVAL, out, "Never fetched the posture")
    assert not check(EVAL, out, "Fetched no collection twice")
    assert not check(EVAL, out, "Sent only")
