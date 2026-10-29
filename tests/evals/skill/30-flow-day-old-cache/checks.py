import re

from grading import (
    TAGS_OK,
    all_exact,
    before,
    fetches,
    has,
    ingest_calls,
    is_kind,
    loaded,
    looked_up,
    refetched,
)


SHOWS = ("aws.sqs",)


def checks(d, t, f):
    r = re.search(r"maxReceiveCount\W+(\d+)", t) or re.search(
        r"max_receive_count\W+(\d+)", t
    )
    return [
        ("Wrote the queue", has(r"aws_sqs_queue", t)),
        (
            "Redrive policy with maxReceiveCount <= 5 and a CMK",
            bool(r) and int(r.group(1)) <= 5 and has(r"kms_master_key_id\s*=", t),
        ),
        ("Tags cost-center and data-classification with a valid value", TAGS_OK(t)),
    ]


def server_checks(log, state, calls):
    return [
        ("Refreshed the posture, once", len(fetches(log, "posture")) == 1),
        (
            "Refreshed the posture before fetching any collection",
            before(log, is_kind("posture"), is_kind("collections")),
        ),
        ("Fetched no collection twice", not refetched(log)),
        ("Sent only the skill's own queries", all_exact(log)),
        (
            "Checked the in-scope collections under the new posture (CIS, Acme Org Standards)",
            loaded(state, "CIS AWS Baseline") and loaded(state, "Acme Org Standards"),
        ),
        # an old posture's guidance may be used while the sync runs (no stalling), but checked
        # again once it has: SKILL.md says `show` again when the sync reports
        (
            "Looked aws.sqs up again once the sync had ingested the collections",
            any(
                i > min(ingest_calls(calls, "collections"), default=len(calls))
                for i in looked_up(calls, "aws.sqs")
            ),
        ),
    ]
