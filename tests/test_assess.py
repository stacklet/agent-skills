import json
import sqlite3
import sys

import _inference
import assess as assess_py
import pytest

from assess import Posture, parse, posture_hash, validate
from conftest import FIXTURE_DEPLOYMENT, cid, gid, put_assessment
from test_digest import add_rollout, same_names
from test_ingest import add_empty_collection, edit_posture, ingest_posture_only
from test_scope import fixture_assessment


# Answers an assessment prompt as a model would: every collection in the digest applies, except
# "require-encryption" (test_digest.add_rollout), which a question adds; or junk, per ASSESS_STUB.
STUB = """
import json, os, re, sys
prompt = sys.stdin.read()
open(os.environ["ASSESS_CALLS"], "a").write("call\\n")
names = re.findall(r"^  - (.+?): \\d+ polic", prompt, re.M)
mode = os.environ.get("ASSESS_STUB", "")
if mode == "junk":
    print("I'd rather not.")
    sys.exit(0)
collections = [
    {"name": n, "default": "ask" if n == "require-encryption" else "applies", "why": "a\\nreason"}
    for n in names
]
if mode == "missing":  # leaves out the one collection the rules don't decide
    collections = [c for c in collections if c["name"] != "require-encryption"]
if mode == "shape":  # valid JSON of the wrong shape
    print('{"collections": 5}')
    sys.exit(0)
print("```json")
print(json.dumps({
    "model": "Stub <b>1.0</b>",
    "collections": collections,
    "questions": [{"id": "batches", "ask": "Is the account in a batch?", "answer_by": "account",
                   "into": ["batch-a", "batch-b"]}] if "require-encryption" in names else [],
    "notes": ["one", "two"],
}))
print("```")
"""

LABELS = {"u1": "CIS AWS Baseline", "u2": "PCI-DSS AWS", "u3": "Sandbox Tagging"}
POSTURE = Posture(
    digest="DIGEST",
    collections=LABELS,
    groups={"g-all": "All AWS", "g-pay": "Payments", "g-sbx": "Sandbox"},
    groups_of={
        "u1": frozenset({"g-all"}),
        "u2": frozenset({"g-pay"}),
        "u3": frozenset({"g-sbx"}),
    },
    everyone=frozenset({"g-all"}),
    ruled={},
)


def answering(seen, *replies):
    """A stand-in for _inference.run: notes each prompt in SEEN and gives REPLIES in turn (the last
    for any call after), each with what it took."""
    left = list(replies)

    def run(prompt, cmd, timeout=300):
        seen.append(prompt)
        return (left.pop(0) if len(left) > 1 else left[0]), {
            "seconds": 1.5,
            "input_tokens": 10,
            "output_tokens": 2,
        }

    return run


def doc(**over):
    d = {
        "model": "M",
        "collections": [
            {"name": "CIS AWS Baseline", "default": "applies", "why": "all accounts"},
            {"name": "PCI-DSS AWS", "default": "ask", "why": "payments only"},
            {"name": "Sandbox Tagging", "default": "ignore", "why": "one sandbox"},
        ],
        "questions": [
            {
                "id": "payments",
                "ask": "Does this touch payments?",
                "answer_by": "user",
                "into": ["Payments"],
            }
        ],
        "notes": [],
    }
    d.update(over)
    return d


def test_a_valid_assessment_is_kept_by_uuid():
    model, a = validate(doc(), POSTURE)
    assert model == "M"
    assert a["collections"]["u2"] == {"default": "ask", "why": "payments only"}
    assert a["questions"] == [
        {
            "id": "payments",
            "ask": "Does this touch payments?",
            "answer_by": "user",
            "into": ["g-pay"],
            "not_into": [],
            "add": ["u2"],
            "remove": [],
        }
    ]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda d: d["collections"].pop(), "not judged: Sandbox Tagging"),
        (
            lambda d: d["collections"].append(dict(d["collections"][0])),
            "judged twice",
        ),
        (
            lambda d: d["collections"][0].update(name="Made Up"),
            "no bound collection is called 'Made Up'",
        ),
        (lambda d: d["collections"][0].update(default="maybe"), "default 'maybe'"),
        (
            lambda d: d.update(questions=[]),
            "'ask' but no question adds them: PCI-DSS AWS",
        ),
        (lambda d: d["questions"][0].update(id="Not A Slug"), "short-kebab-slug"),
        (lambda d: d["questions"][0].update(answer_by="oracle"), "answer_by 'oracle'"),
        (
            lambda d: d["questions"].append(
                {"id": "nothing", "ask": "?", "answer_by": "user"}
            ),
            "names no group",
        ),
        (
            lambda d: d["questions"][0].update(ask=["not", "text"]),
            "ask: missing or not text",
        ),
        (lambda d: d["questions"][0].update(ask="  "), "ask: missing or not text"),
        # shapes a model may give: each one a refusal, never a crash
        (lambda d: d.update(collections=5), "collections is not a list"),
        (lambda d: d.update(questions={"id": "x"}), "questions is not a list"),
        (lambda d: d.update(notes="abc"), "notes is not a list"),
        (lambda d: d.update(notes=[{"a": 1}]), "notes: missing or not text"),
        (lambda d: d["collections"].append("CIS AWS Baseline"), "is not an object"),
        (lambda d: d["questions"].append(7), "is not an object"),
        # an id that isn't a string isn't hashable either: refused, not a crash
        (lambda d: d["questions"][0].update(id=["payments"]), "short-kebab-slug"),
        (lambda d: d["questions"][0].update(id={"a": 1}), "short-kebab-slug"),
        (
            lambda d: d.update(
                questions=[
                    {
                        "id": f"q{i}",
                        "ask": "?",
                        "answer_by": "user",
                        "into": ["Payments"],
                    }
                    for i in range(21)
                ]
            ),
            "21 questions, more than 20",
        ),
        (lambda d: d["questions"][0].update(into="Payments"), "into is not a list"),
        (
            lambda d: d["questions"][0].update(into=["Made Up"]),
            "into: no bound group is called 'Made Up'",
        ),
        # a yes must change something: adding only what applies anyway, or the same both ways
        (
            lambda d: (
                d["questions"][0].update(into=["Payments", "All AWS"])
                or d["questions"].append(
                    {
                        "id": "moot",
                        "ask": "?",
                        "answer_by": "user",
                        "into": ["All AWS"],
                    }
                )
            ),
            r"questions\[1\]: its answer changes nothing",
        ),
        (
            lambda d: d["questions"][0].update(not_into=["Payments"]),
            "names a group as both into and not_into",
        ),
    ],
)
def test_an_assessment_that_does_not_cover_the_posture_is_rejected(change, problem):
    d = doc()
    change(d)
    with pytest.raises(ValueError, match=problem):
        validate(d, POSTURE)


def test_model_text_is_cut_to_one_short_line():
    d = doc(notes=["line one\n\nline two " + "x" * 400] * 20)
    d["collections"][0]["why"] = "two\nlines"
    _, a = validate(d, POSTURE)
    assert a["collections"]["u1"]["why"] == "two lines"
    assert len(a["notes"]) == 8
    assert all("\n" not in n and len(n) <= 300 for n in a["notes"])


def test_json_is_found_inside_a_fence_or_words():
    assert parse('Here:\n```json\n{"a": 1}\n```\nDone.') == {"a": 1}
    with pytest.raises(ValueError, match="no JSON object"):
        parse("I'd rather not.")
    with pytest.raises(ValueError, match="no JSON object"):
        parse("[1, 2]")


class Rows:
    """A stand-in db returning ROWS for any query."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, _):
        return iter(self.rows)


def test_the_key_does_not_depend_on_row_order():
    rows = [("c1", "g1"), ("c1", "g2"), ("c2", "g1")]
    assert posture_hash(Rows(rows)) == posture_hash(Rows(rows[::-1]))
    assert posture_hash(Rows(rows)) != posture_hash(Rows(rows[:2]))


def grow(b):
    """Five more accounts in every group."""
    for g in b["accountGroups"]:
        g["accountMappings"]["pageInfo"]["total"] += 5


@pytest.fixture
def assess(run, fx, tmp_path):
    """Run assess.py with STUB as the inference command, on the fixture's posture plus a rollout."""
    stub = tmp_path / "stub.py"
    stub.write_text(STUB)
    calls = tmp_path / "calls"
    calls.touch()
    edit_posture(fx, add_rollout)
    ingest_posture_only(run, fx, tmp_path)

    def go(*args, **env):
        return run(
            "assess.py",
            *args,
            STACKLET_GUARDRAILS_INFERENCE_CMD=json.dumps([sys.executable, str(stub)]),
            ASSESS_CALLS=str(calls),
            **env,
        )

    go.calls = lambda: len(calls.read_text().split())
    go.cmd = json.dumps([sys.executable, str(stub)])
    go.fx, go.tmp = fx, tmp_path
    return go


def test_the_assessment_is_made_once_per_posture_shape(assess, run):
    r = assess("show")
    assert r.returncode == 0, r.stderr
    assert "by Stub b1.0/b (custom command)" in r.stdout
    assert (
        "## Depends on the answers below\n- require-encryption: a reason\n" in r.stdout
    )
    assert (
        "- batches (account): Is the account in a batch?\n"
        "  - yes adds: require-encryption\n" in r.stdout
    )
    assert assess("show").stdout == r.stdout
    assert assess().stdout == r.stdout  # show is the default
    assert assess.calls() == 1

    # group sizes move with every account created: no new judgement
    edit_posture(assess.fx, grow)
    assert run("ingest.py", "posture", assess.fx / "posture.json").returncode == 0
    assess("show")
    assert assess.calls() == 1

    # nor do renames, descriptions or filters: only the set of bindings is the key
    def redefine(b):
        for c in b["policyCollections"]:
            c["name"] += " (renamed)"
        for g in b["accountGroups"]:
            g["dynamicFilter"] = "tag:x = y"
            g["description"] = "changed"

    edit_posture(assess.fx, redefine)
    assert run("ingest.py", "posture", assess.fx / "posture.json").returncode == 0
    r = assess("show")
    assert "require-encryption (renamed)" in r.stdout  # shown under today's names
    assert assess.calls() == 1

    # a binding gone is a new set
    edit_posture(assess.fx, lambda b: b["bindings"].pop())
    assert run("ingest.py", "posture", assess.fx / "posture.json").returncode == 0
    assess("show")
    assert assess.calls() == 2

    assess("show", "--reassess")
    assert assess.calls() == 3


@pytest.mark.parametrize("mode", ["junk", "missing", "shape"])
def test_output_rejected_twice_is_not_cached(assess, mode):
    r = assess("show", ASSESS_STUB=mode)
    assert r.returncode != 0
    assert "assessment rejected twice, nothing cached" in r.stderr
    assert "Traceback" not in r.stderr
    assert assess.calls() == 2
    # nothing remembered: the next show (a sync's) tries again
    assert assess("show").returncode == 0
    assert assess.calls() == 3


def test_check_falls_back_to_the_defaults_when_none_could_be_made(assess, run):
    assert assess("show", ASSESS_STUB="shape").returncode != 0
    r = run("scope.py", "check")
    assert "Traceback" not in r.stderr
    report = json.loads(r.stdout)
    assert report["assessment"].startswith("none made yet")
    assert sorted(c["name"] for c in report["in_scope"].values()) == [
        "Acme Org Standards",
        "CIS AWS Baseline",
    ]


def test_an_assessment_another_session_made_meanwhile_is_kept(
    assess, open_cache, monkeypatch
):
    # its questions may be answered already: a second one replacing it would strand those
    db = open_cache()
    first = {"collections": {}, "questions": [], "notes": ["first"]}

    def made_meanwhile(posture, cmd, earlier=None):
        # the other session's lands while this one waits on its model
        with db:
            db.execute(
                "INSERT INTO assessment VALUES (?,?,?,?,?,?,?)",
                (
                    assess_py.posture_hash(db),
                    "d",
                    json.dumps(first),
                    '{"argv": [], "custom": false}',
                    "A",
                    "t",
                    None,
                ),
            )
        return "B", {"collections": {}, "questions": [], "notes": ["second"]}, {}

    monkeypatch.setattr(assess_py, "ask_model", made_meanwhile)
    monkeypatch.setattr(
        assess_py, "command", lambda task: _inference.Command(["x"], custom=True)
    )
    row = assess_py.assessment(db)
    assert json.loads(row["assessment"])["notes"] == ["first"]
    # a deliberate --reassess does replace it
    monkeypatch.setattr(
        assess_py,
        "ask_model",
        lambda posture, cmd, earlier=None: (
            "C",
            {"collections": {}, "questions": [], "notes": ["third"]},
            {},
        ),
    )
    assert json.loads(assess_py.assessment(db, reassess=True)["assessment"])[
        "notes"
    ] == ["third"]


def test_the_digest_shown_is_the_one_the_assessment_was_made_from(assess, run):
    assert assess("show").returncode == 0
    made_from = assess("show", "--digest").stdout
    assert made_from == run("posture.py", "--digest").stdout

    # sizes change no assessment, so the stored digest keeps the sizes it was judged on
    edit_posture(assess.fx, grow)
    assert run("ingest.py", "posture", assess.fx / "posture.json").returncode == 0
    assert assess("show", "--digest").stdout == made_from
    assert run("posture.py", "--digest").stdout != made_from
    assert assess.calls() == 1


RULED = {
    "u1": {
        "default": "applies",
        "why": "Bound to every account in its provider, present and future.",
    },
    "u3": {"default": "ignore", "why": "It holds no policies."},
}
RULED_POSTURE = POSTURE._replace(ruled=RULED)


def test_rule_decided_collections_need_no_judgement_and_overrule_one():
    d = doc()
    d["collections"] = [
        {"name": "PCI-DSS AWS", "default": "ask", "why": "payments"},
        # judged anyway, against the rule: the rule stands
        {"name": "Sandbox Tagging", "default": "applies", "why": "x"},
    ]
    _, a = validate(d, RULED_POSTURE)
    assert a["collections"]["u1"] == RULED["u1"]
    assert a["collections"]["u3"] == RULED["u3"]
    assert a["collections"]["u2"]["default"] == "ask"
    with pytest.raises(ValueError, match="not judged: PCI-DSS AWS"):
        validate(doc(collections=[]), RULED_POSTURE)


def test_the_prompt_lists_what_the_rules_decided(monkeypatch):
    seen = []
    monkeypatch.setattr(assess_py, "run", answering(seen, json.dumps(doc())))
    assess_py.ask_model(RULED_POSTURE, ["x"], None)
    assert "- CIS AWS Baseline: applies (Bound to every account" in seen[0]
    assert "- Sandbox Tagging: ignore (It holds no policies.)" in seen[0]
    assert "- (none: this is the first assessment)" in seen[0]


def test_all_accounts_and_empty_collections_are_decided_by_rule(
    run, fx, tmp_path, open_cache
):
    add_empty_collection(fx)
    ingest_posture_only(run, fx, tmp_path)
    ruled = assess_py.ruled(open_cache())
    assert {u: c["default"] for u, c in ruled.items()} == {
        cid("CIS AWS Baseline"): "applies",
        cid("Acme Org Standards"): "applies",
        # empty, though bound to all accounts: it guards nothing
        "uuid-empty": "ignore",
    }


def test_a_binding_to_a_group_the_posture_lacks_counts_for_nothing(
    run, fx, tmp_path, open_cache
):
    # an empty collection bound only to such a group isn't bound at all, as in the digest: a rule
    # judging it would leave the assessment a collection the digest never names
    add_empty_collection(fx)

    def stray(b):
        for x in b["bindings"]:
            if x["policyCollection"]["uuid"] == "uuid-empty":
                x["accountGroup"]["uuid"] = "no-such-group"

    edit_posture(fx, stray)
    ingest_posture_only(run, fx, tmp_path)
    db = open_cache()
    assert "uuid-empty" not in assess_py.ruled(db)
    assert set(assess_py.ruled(db)) <= set(assess_py.collection_labels(db))


def test_a_posture_the_rules_decide_makes_no_model_call(run, fx, tmp_path):
    # only the collections bound to all accounts stay bound
    def keep_all_accounts(b):
        everyone = next(
            g["uuid"] for g in b["accountGroups"] if g["dynamicFilter"] == ""
        )
        b["bindings"] = [
            x for x in b["bindings"] if x["accountGroup"]["uuid"] == everyone
        ]

    edit_posture(fx, keep_all_accounts)
    ingest_posture_only(run, fx, tmp_path)
    # the harness's inference command fails: none is run
    r = run("assess.py", "show")
    assert r.returncode == 0, r.stderr
    assert "by rules alone (nothing for a model to judge)" in r.stdout
    assert "- CIS AWS Baseline: Bound to every account" in r.stdout


def test_a_reassessment_is_offered_the_earlier_questions(
    assess, monkeypatch, open_cache
):
    # repos' answers count only while a question keeps its id and wording
    assert assess("show").returncode == 0  # the first: no earlier questions
    db = open_cache()
    earlier = assess_py.earlier_questions(db, assess_py.read_posture(db))
    assert earlier == (
        "- batches: Is the account in a batch? (answer_by account; into: batch-a, batch-b; "
        "not_into: -)"
    )
    seen = []
    monkeypatch.setattr(
        assess_py,
        "run",
        answering(
            seen,
            json.dumps(
                {
                    "collections": [
                        {"name": n, "default": d, "why": "w"}
                        for n, d in (
                            ("require-encryption", "ask"),
                            ("PCI-DSS AWS", "ignore"),
                            ("Sandbox Tagging", "ignore"),
                        )
                    ],
                    "questions": [
                        {
                            "id": "batches",
                            "ask": "Is the account in a batch?",
                            "answer_by": "account",
                            "into": ["batch-a", "batch-b"],
                        }
                    ],
                }
            ),
        ),
    )
    monkeypatch.setattr(
        assess_py, "command", lambda task: _inference.Command(["x"], custom=True)
    )
    assess_py.assessment(db, reassess=True)
    assert "- batches: Is the account in a batch?" in seen[0]
    assert "never give its id to a different question" in seen[0]


def test_a_retry_says_why_the_first_reply_was_rejected(monkeypatch):
    # a model that left a collection out once tends to again unless told
    seen = []
    first = doc(collections=doc()["collections"][1:])
    monkeypatch.setattr(
        assess_py, "run", answering(seen, json.dumps(first), json.dumps(doc()))
    )
    _, _, stats = assess_py.ask_model(POSTURE, ["x"], None)
    reason = "<rejection>\nnot judged: CIS AWS Baseline\n</rejection>"
    assert reason not in seen[0]
    # in a fence, as data: it quotes model-written values
    assert seen[1].endswith(reason)
    # what both attempts took
    assert stats == {
        "attempts": 2,
        "seconds": 3.0,
        "input_tokens": 20,
        "output_tokens": 4,
    }


def test_what_an_assessment_took_is_recorded_and_shown(assess, cache):
    r = assess("show")
    assert r.returncode == 0, r.stderr
    db = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "guardrails.db")
    stats = json.loads(db.execute("SELECT stats FROM assessment").fetchone()[0])
    # a custom command writes only its answer: only its time is known
    assert set(stats) == {"attempts", "seconds"}
    assert stats["attempts"] == 1
    assert f"; {stats['seconds']:.0f}s)" in r.stdout


def test_a_question_cannot_remove_what_applies_to_every_account():
    # no account opts out of a group of every account, whatever a question says
    d = doc()
    d["questions"].append(
        {
            "id": "sandbox-only",
            "ask": "?",
            "answer_by": "user",
            "not_into": ["All AWS"],
        }
    )
    with pytest.raises(
        ValueError, match=r"not_into names a group of every account \(All AWS\)"
    ):
        validate(d, RULED_POSTURE)


def test_adding_only_an_empty_collection_changes_nothing():
    d = doc()
    d["questions"].append(
        {"id": "sandbox", "ask": "?", "answer_by": "user", "into": ["Sandbox"]}
    )
    with pytest.raises(ValueError, match=r"questions\[1\]: its answer changes nothing"):
        validate(d, RULED_POSTURE)


def test_model_written_values_are_cut_short_in_reasons():
    d = doc()
    d["collections"][0]["name"] = "x" * 500
    # quoted back to the model in a retry: never at full length
    with pytest.raises(
        ValueError, match=r"no bound collection is called 'x{76}\.\.\.(;|$)"
    ):
        validate(d, POSTURE)


def test_earlier_questions_come_from_the_newest_assessment_that_asked_any(
    cache, ingested, open_cache
):
    put_assessment(cache, fixture_assessment())
    # a newer one, decided by rules alone: no questions to keep
    db = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "guardrails.db")
    with db:
        db.execute(
            "INSERT INTO assessment VALUES ('newer', 'd', ?, ?, 'r', '2030-01-01T00:00:00Z', NULL)",
            (
                json.dumps({"collections": {}, "questions": [], "notes": []}),
                _inference.Command([], custom=False).recorded(),
            ),
        )
    db = open_cache()
    earlier = assess_py.earlier_questions(db, assess_py.read_posture(db))
    assert earlier.startswith(
        "- payments: Does this code deploy into a payments account?"
    )


def test_what_a_yes_changes_follows_from_the_groups_a_question_names():
    # a yes adds what is bound to any group named, and removes what is bound only to those named
    labels = {"u1": "Base", "u2": "Rollout", "u3": "Spread", "u4": "Org-wide"}
    posture = Posture(
        digest="DIGEST",
        collections=labels,
        groups={g: g for g in ("All AWS", "OU-a", "OU-b", "Org", "Paused")},
        groups_of={
            "u1": frozenset({"All AWS"}),
            "u2": frozenset({"OU-a", "OU-b"}),  # a rollout over two groups
            "u3": frozenset({"OU-b", "Org"}),  # reaches OU-b, and the whole org too
            "u4": frozenset({"Org"}),
        },
        everyone=frozenset({"All AWS"}),
        ruled={},
    )
    d = {
        "collections": [
            {"name": n, "default": dflt, "why": "w"}
            for n, dflt in (
                ("Base", "applies"),
                ("Rollout", "ask"),
                ("Spread", "applies"),
                ("Org-wide", "applies"),
            )
        ],
        "questions": [
            {"id": "ou-b", "ask": "In OU-b?", "answer_by": "user", "into": ["OU-b"]},
            {
                "id": "outside-org",
                "ask": "Only outside the org?",
                "answer_by": "user",
                "not_into": ["Org", "OU-b"],
            },
        ],
    }
    _, a = validate(d, posture)
    ou_b, outside = a["questions"]
    assert ou_b["add"] == [
        "u2",
        "u3",
    ]  # everything bound to OU-b, whatever else it reaches
    # only what reaches accounts through these groups alone: Rollout also reaches OU-a
    assert outside["remove"] == ["u3", "u4"]
    assert outside["not_into"] == ["OU-b", "Org"]  # by uuid


def test_a_question_names_a_group_by_the_digest_s_label(assess, run, open_cache):
    # two groups called the same: the label with its uuid picks one
    edit_posture(assess.fx, same_names)
    assert run("ingest.py", "posture", assess.fx / "posture.json").returncode == 0
    db = open_cache()
    posture = assess_py.read_posture(db)
    labels, ruled = posture.collections, posture.ruled
    reply = {
        "collections": [
            {
                "name": n,
                "default": "ask" if n == "require-encryption" else "ignore",
                "why": "w",
            }
            for u, n in labels.items()
            if u not in ruled
        ],
        "questions": [
            {
                "id": "wave-b",
                "ask": "In wave b?",
                "answer_by": "account",
                "into": [f"batch-a [{gid('batch-b')}]"],
            }
        ],
    }
    _, a = validate(reply, posture)
    assert a["questions"][0]["into"] == [gid("batch-b")]
    assert a["questions"][0]["add"] == ["c-rollout"]
    reply["questions"][0]["into"] = ["batch-a"]  # which one?
    with pytest.raises(ValueError, match="no bound group is called 'batch-a'"):
        validate(reply, posture)


def test_a_retry_gets_what_is_left_of_the_budget(monkeypatch):
    # two attempts at up to 300s each would outlast the 10 minutes a command gets (references/sync.md
    # gives `assess.py show` that timeout)
    timeouts = []
    replies = [json.dumps(doc(collections=doc()["collections"][1:])), json.dumps(doc())]

    def run(prompt, cmd, timeout=300):
        timeouts.append(timeout)
        return replies.pop(0), {"seconds": 1}

    monkeypatch.setattr(assess_py, "run", run)
    assess_py.ask_model(POSTURE, ["x"], None)
    assert timeouts[0] == 300
    # what is left after the first, which (stubbed) took no time
    assert assess_py.BUDGET - 1 < timeouts[1] <= assess_py.BUDGET
    assert assess_py.BUDGET < 600
