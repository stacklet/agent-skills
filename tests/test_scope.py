import hashlib
import json
import os
import sqlite3
import tempfile

from pathlib import Path

import _cache
import pytest
import scope

from assess import Posture, validate
from conftest import FIXTURE_DEPLOYMENT, cid, gid, put_assessment
from test_ingest import add_empty_collection, edit_posture, ingest_posture_only


ROLLOUT = "Acme Rollout"


def add_opt_out(b):
    """An org-standards rollout exempt accounts opt out of: a group of the accounts not tagged
    exempt, and a collection (the org standards' policies) bound to it alone, which applies by
    judgement rather than by rule."""
    b["accountGroups"].append(
        {
            "uuid": gid("aws-not-exempt"),
            "name": "aws-not-exempt",
            "provider": "AWS",
            "description": None,
            "dynamicFilter": "tag:exempt != true",
            "regions": [],
            "accountMappings": {"pageInfo": {"total": 38}},
        }
    )
    acme = next(c for c in b["policyCollections"] if c["name"] == "Acme Org Standards")
    b["policyCollections"].append(
        {**acme, "id": "id-rollout", "uuid": cid(ROLLOUT), "name": ROLLOUT}
    )
    b["bindings"].append(
        {
            "uuid": "b-rollout",
            "name": "rollout",
            "description": None,
            "lastDeployed": "2026-10-01T00:00:00Z",
            "accountGroup": {"uuid": gid("aws-not-exempt")},
            "policyCollection": {"uuid": cid(ROLLOUT)},
        }
    )


def fixture_assessment(asked=True):
    """An assessment of the fixture posture plus add_opt_out, made as assess.validate makes one from
    a model's reply: the all-accounts collections apply (by rule), and so does the rollout;
    PCI-DSS depends on a payments question (ASKED, else it is ignored); the one-person sandbox is
    ignored; and an opt-out question removes the rollout."""
    bound = {
        "CIS AWS Baseline": "All aws accounts",
        "Acme Org Standards": "All aws accounts",
        "PCI-DSS AWS": "aws-payments",
        "Sandbox Tagging": "aws-sandbox-jdoe",
        ROLLOUT: "aws-not-exempt",
    }
    ruled = {
        cid(c): {
            "default": "applies",
            "why": "Bound to every account in its provider, present and future.",
        }
        for c in ("CIS AWS Baseline", "Acme Org Standards")
    }
    posture = Posture(
        digest="digest",
        collections={cid(c): c for c in bound},
        groups={gid(g): g for g in set(bound.values())},
        groups_of={cid(c): frozenset({gid(g)}) for c, g in bound.items()},
        everyone=frozenset({gid("All aws accounts")}),
        ruled=ruled,
    )
    reply = {
        "collections": [
            {
                "name": "PCI-DSS AWS",
                "default": "ask" if asked else "ignore",
                "why": "payments accounts only",
            },
            {"name": "Sandbox Tagging", "default": "ignore", "why": "one sandbox"},
            {"name": ROLLOUT, "default": "applies", "why": "all but exempt accounts"},
        ],
        "questions": [
            {
                "id": "payments",
                "ask": "Does this code deploy into a payments account?",
                "answer_by": "either",
                "into": ["aws-payments"],
            },
            {
                "id": "only-exempt",
                "ask": "Does this code deploy only into accounts exempt from org standards?",
                "answer_by": "account",
                "not_into": ["aws-not-exempt"],
            },
        ]
        if asked
        else [],
        "notes": ["a note"],
    }
    return validate(reply, posture)[1]


@pytest.fixture
def opted_out(ingested, ingest, fx):
    """The fixture posture plus add_opt_out, ingested with its policies."""
    edit_posture(fx, add_opt_out)
    assert ingest(fx).returncode == 0
    return fx


@pytest.fixture
def assessed(opted_out, cache):
    put_assessment(cache, fixture_assessment())


@pytest.fixture
def check(assessed, run):
    return lambda: run("scope.py", "check")


@pytest.fixture
def decide(assessed, run):
    return lambda *args: run("scope.py", "decide", *args, "--because", "t")


@pytest.fixture
def checked(check):
    """`scope.py check`'s output, parsed."""
    return lambda: json.loads(check().stdout)


def answer_all(decide, payments="no"):
    assert decide("--answer", f"payments={payments}", "only-exempt=no").returncode == 0


@pytest.mark.parametrize(
    "text",
    [
        "[1, 2]",
        "{not json",
        '{"decisions": ["x"]}',
        '{"decisions": "abc"}',
        '{"deployment": 5}',
        '{"decisions": [{"collection": "A", "decision": "maybe"}]}',
        '{"answers": [{"question": "payments", "answer": "probably"}]}',
        '{"answers": [{"question": "payments"}]}',
    ],
)
def test_malformed_files_are_reported_not_tracebacks(check, scope_file, text):
    scope_file.parent.mkdir(parents=True)
    scope_file.write_text(text)
    r = check()
    assert "Traceback" not in r.stderr
    assert r.returncode == 1
    report = json.loads(r.stdout)
    assert report["scope_file_invalid"]
    # there is a file, just not a usable one: never said to be none
    assert report["new_bindings"] == "scope file ignored"


def test_decide_will_not_overwrite_a_file_it_cannot_read(decide, scope_file):
    scope_file.parent.mkdir(parents=True)
    scope_file.write_text("{not json")
    r = decide("--include", cid("PCI-DSS AWS"))
    assert r.returncode != 0
    assert scope_file.read_text() == "{not json"


def test_decide_writes_the_scope_file_with_the_usual_mode(decide, scope_file):
    # it is written through a temp file, which mkstemp makes 0600; the committed file shouldn't be
    umask = os.umask(0o022)
    try:
        r = decide("--include", cid("PCI-DSS AWS"), "--because", "card data")
    finally:
        os.umask(umask)
    assert r.returncode == 0, r.stderr
    assert scope_file.stat().st_mode & 0o777 == 0o644


def test_decide_refuses_a_symlinked_scope_file(decide, scope_file, tmp_path):
    target = tmp_path / "victim.json"
    target.write_text("{}")
    scope_file.parent.mkdir(parents=True)
    scope_file.symlink_to(target)
    r = decide("--include", cid("PCI-DSS AWS"))
    assert r.returncode != 0
    assert "symlink" in r.stderr
    assert target.read_text() == "{}"


def test_decide_refuses_a_symlinked_stacklet_dir(decide, repo, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (repo / ".stacklet").symlink_to(elsewhere)
    assert decide("--include", cid("PCI-DSS AWS")).returncode != 0


def test_excluding_what_applies_is_surfaced(run, checked, decide):
    r = decide("--exclude", cid("CIS AWS Baseline"), "--include", cid("PCI-DSS AWS"))
    assert r.returncode == 0
    out = checked()
    assert out["excluded_applicable"][0]["name"] == "CIS AWS Baseline"
    assert out["excluded_applicable"][0]["because"] == "t"
    r = run("distill.py", "index")
    assert "excludes collection 'CIS AWS Baseline'" in r.stderr


def test_check_says_which_resource_types_a_question_covers(checked):
    # so an agent can tell a question that can't affect the change at hand from one that can
    q = checked()["unanswered_questions"]["payments"]
    assert q["resource_types"] == {"aws.rds": 1, "aws.s3": 2}
    assert q["resource_types_complete"] is True
    assert q["yes_adds"] == ["PCI-DSS AWS"]
    assert q["yes_removes"] == []
    assert q["answer_by"] == "either"


def test_a_new_binding_alone_does_not_fail_check(check, decide, scope_file):
    answer_all(decide)
    assert check().returncode == 0
    p = json.loads(scope_file.read_text())
    p["seen_bindings"] = p["seen_bindings"][:-1]
    scope_file.write_text(json.dumps(p))
    r = check()
    assert r.returncode == 0
    assert json.loads(r.stdout)["new_bindings"]


def test_scope_file_is_found_from_a_subdirectory(ingested, run, repo, scope_file):
    (repo / ".git").mkdir()
    sub = repo / "modules" / "x"
    sub.mkdir(parents=True)
    r = run(
        "scope.py", "decide", "--include", cid("PCI-DSS AWS"), "--because", "t", cwd=sub
    )
    assert r.returncode == 0
    assert scope_file.is_file()
    assert not (sub / ".stacklet").exists()


def test_a_renamed_collection_keeps_its_decision(
    run, ingest, checked, decide, fx, scope_file
):
    assert decide("--include", cid("PCI-DSS AWS")).returncode == 0

    def rename(b):
        for c in b["policyCollections"]:
            if c["name"] == "PCI-DSS AWS":
                c["name"] = "Card Data"

    edit_posture(fx, rename)
    assert ingest(fx).returncode == 0
    assert checked()["in_scope"][cid("PCI-DSS AWS")]["name"] == "Card Data"
    assert decide("--include", cid("PCI-DSS AWS")).returncode == 0
    names = [x.get("name") for x in json.loads(scope_file.read_text())["decisions"]]
    # the readers' copy is kept current
    assert names == ["Card Data"]


@pytest.mark.parametrize("version", [1, 4, 6])
def test_a_scope_file_of_another_version_is_invalid(check, scope_file, version):
    scope_file.parent.mkdir(parents=True)
    scope_file.write_text(json.dumps({"version": version, "decisions": []}))
    r = check()
    assert r.returncode == 1
    assert f"version {version} is not one this skill reads" in r.stdout


# Account facts: an account the repo deploys to adds what is bound to its groups, on top of the
# posture.


def account_response(key, groups, active=True, tags=None):
    """What account.graphql returns for an AWS account KEY in the fixture groups named GROUPS."""
    edges = [{"node": {"group": {"uuid": gid(g), "name": g}}} for g in groups]
    return {
        "data": {
            "platform": {"urls": {"console": f"https://{FIXTURE_DEPLOYMENT}/"}},
            "account": {
                "key": key,
                "name": f"acct-{key}",
                "provider": "AWS",
                "path": "/Apps/",
                "orgId": "o-test",
                "active": active,
                "tags": tags,
                "groupMappings": {
                    "pageInfo": {"total": len(edges), "hasNextPage": False},
                    "edges": edges,
                },
            },
        }
    }


@pytest.fixture
def look_up(ingested, run, tmp_path):
    """Record account.graphql responses as an agent would, through `ingest.py accounts`."""

    def look_up(*responses):
        d = Path(tempfile.mkdtemp(dir=tmp_path))
        for i, r in enumerate(responses):
            (d / f"account_{i}.json").write_text(json.dumps(r))
        return run("ingest.py", "accounts", *sorted(d.glob("account_*.json")))

    return look_up


def test_an_account_in_a_group_brings_in_its_collections(look_up, checked, decide):
    look_up(account_response("111", ["All aws accounts", "aws-payments"]))
    assert decide("--account", "aws:111").returncode == 0
    out = checked()
    # which pre-answers the payments question: a yes can add nothing more
    assert "payments" not in out["unanswered_questions"]
    assert cid("PCI-DSS AWS") in out["from_accounts"]
    assert out["accounts"]["AWS:111"]["status"] == "current"


def test_an_account_outside_a_group_rules_nothing_out(look_up, check, decide):
    # the code may deploy elsewhere too: a question about what it doesn't reach is still asked
    look_up(account_response("222", ["All aws accounts"]))
    decide("--account", "AWS:222")
    r = check()
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert "payments" in out["unanswered_questions"]
    assert out["from_accounts"] == {}


def test_an_account_not_looked_up_adds_nothing_and_is_said(run, check, decide):
    decide("--account", "AWS:333")
    r = check()
    out = json.loads(r.stdout)
    assert out["accounts"]["AWS:333"] == {"status": "not looked up"}
    assert r.returncode == 1
    assert "payments" in out["unanswered_questions"]
    assert "not looked up yet" in run("distill.py", "index").stderr


def test_an_exclusion_beats_an_account_and_is_surfaced(look_up, checked, decide):
    look_up(account_response("111", ["All aws accounts", "aws-payments"]))
    decide("--account", "AWS:111", "--exclude", cid("PCI-DSS AWS"))
    out = checked()
    assert cid("PCI-DSS AWS") not in out["from_accounts"]
    assert [x["name"] for x in out["excluded_applicable"]] == ["PCI-DSS AWS"]


def test_a_refresh_keeps_looked_up_accounts(ingest, look_up, checked, decide):
    look_up(account_response("111", ["All aws accounts", "aws-payments"]))
    decide("--account", "AWS:111")
    assert ingest().returncode == 0
    out = checked()
    assert out["accounts"]["AWS:111"]["status"] == "current"
    assert cid("PCI-DSS AWS") in out["from_accounts"]


def test_an_unknown_account_is_skipped_and_said(look_up):
    r = look_up(
        {
            "data": {
                "platform": {"urls": {"console": FIXTURE_DEPLOYMENT}},
                "account": None,
            }
        }
    )
    assert r.returncode == 0, r.stderr
    assert "holds no account" in r.stderr


def test_accounts_are_recorded_with_names_and_can_be_forgotten(
    look_up, decide, scope_file
):
    look_up(account_response("111", ["All aws accounts"]))
    decide("--account", "AWS:111")
    assert json.loads(scope_file.read_text())["accounts"] == [
        {"provider": "AWS", "key": "111", "name": "acct-111", "because": "t"}
    ]
    decide("--forget-account", "AWS:111")
    assert json.loads(scope_file.read_text())["accounts"] == []
    r = decide("--account", "111")
    assert r.returncode != 0
    assert "expected PROVIDER:KEY" in r.stderr


def test_an_account_brings_in_even_an_ignored_collection(look_up, checked, decide):
    # "ignore" is a judgement about most code; an account in its group settles it for this code
    look_up(account_response("444", ["All aws accounts", "aws-sandbox-jdoe"]))
    decide("--account", "AWS:444")
    assert cid("Sandbox Tagging") in checked()["from_accounts"]


def test_accounts_widen_what_distill_shows(run, look_up, decide):
    before = run("distill.py", "index").stdout
    look_up(account_response("111", ["All aws accounts", "aws-payments"]))
    decide("--account", "AWS:111")
    assert run("distill.py", "index").stdout != before


def test_a_fresh_lookup_replaces_the_old_groups(look_up, checked, decide):
    look_up(account_response("111", ["All aws accounts", "aws-payments"]))
    decide("--account", "AWS:111")
    # left the payments group
    look_up(account_response("111", ["All aws accounts"]))
    assert cid("PCI-DSS AWS") not in checked()["from_accounts"]


def test_a_bad_lookup_is_said_and_the_others_still_load(look_up, checked, decide):
    bad = account_response("222", ["All aws accounts"])
    bad["data"]["account"]["groupMappings"]["pageInfo"]["hasNextPage"] = True
    res = look_up(account_response("111", ["All aws accounts", "aws-payments"]), bad)
    assert res.returncode != 0
    assert "account_1.json: ValueError" in res.stderr
    assert "truncated groupMappings" in res.stderr
    decide("--account", "AWS:111", "AWS:222")
    out = checked()
    assert out["accounts"]["AWS:111"]["status"] == "current"
    assert out["accounts"]["AWS:222"] == {"status": "not looked up"}


def test_an_inactive_account_is_said_and_adds_nothing(look_up, checked, decide):
    look_up(account_response("555", [], active=False))
    decide("--account", "AWS:555")
    out = checked()
    assert out["accounts"]["AWS:555"]["active"] is False
    assert out["from_accounts"] == {}


def test_a_stale_snapshot_still_counts_and_is_reported(
    run, look_up, checked, decide, cache
):
    look_up(account_response("111", ["All aws accounts", "aws-payments"]))
    decide("--account", "AWS:111")
    con = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "guardrails.db")
    con.execute("UPDATE account SET looked_up = '2020-01-01T00:00:00Z'")
    con.commit()
    con.close()
    out = checked()
    assert out["accounts"]["AWS:111"]["status"] == "stale"
    assert cid("PCI-DSS AWS") in out["from_accounts"]
    r = run("distill.py", "index")
    assert "looked up more than a day ago: AWS:111" in r.stderr


# Answers to the posture assessment's questions.


def test_the_defaults_apply_until_questions_are_answered(checked):
    out = checked()
    assert sorted(c["name"] for c in out["in_scope"].values()) == [
        "Acme Org Standards",
        ROLLOUT,
        "CIS AWS Baseline",
    ]
    assert sorted(out["unanswered_questions"]) == ["only-exempt", "payments"]
    assert out["assessment"] == "by Fixture Model, 2026-10-01T00:00:00Z"
    assert out["assessment_notes"] == ["a note"]


def test_a_yes_adds_what_its_question_says(checked, check, decide):
    answer_all(decide, payments="yes")
    assert check().returncode == 0
    out = checked()
    assert cid("PCI-DSS AWS") in out["in_scope"]
    assert out["unanswered_questions"] == {}
    assert out["answers"] == {"only-exempt": "no", "payments": "yes"}


@pytest.mark.parametrize("answer", ["no", "unknown"])
def test_no_or_not_sure_adds_nothing_but_settles_the_question(checked, decide, answer):
    decide("--answer", f"payments={answer}")
    out = checked()
    assert cid("PCI-DSS AWS") not in out["in_scope"]
    assert "payments" not in out["unanswered_questions"]


def test_a_yes_can_remove_what_otherwise_applies(checked, decide):
    decide("--answer", "only-exempt=yes")
    out = checked()
    assert cid(ROLLOUT) not in out["in_scope"]
    # bound to every account: no opting out
    assert cid("Acme Org Standards") in out["in_scope"]


def test_an_account_beats_a_removal(look_up, checked, decide):
    # code deploying into an account in the group still meets that group's guardrails
    look_up(account_response("111", ["All aws accounts", "aws-not-exempt"]))
    decide("--answer", "only-exempt=yes", "--account", "AWS:111")
    assert cid(ROLLOUT) in checked()["in_scope"]


def test_answers_are_recorded_with_the_question_and_can_be_forgotten(
    decide, scope_file
):
    decide("--answer", "payments=yes")
    assert json.loads(scope_file.read_text())["answers"] == [
        {
            "question": "payments",
            "name": "Does this code deploy into a payments account?",
            "answer": "yes",
            "because": "t",
        }
    ]
    decide("--forget-answer", "payments")
    assert json.loads(scope_file.read_text())["answers"] == []


@pytest.mark.parametrize(
    ("arg", "error"),
    [
        ("nosuch=yes", "unknown question 'nosuch'"),
        ("payments=maybe", "expected QUESTION=ANSWER"),
        ("payments", "expected QUESTION=ANSWER"),
    ],
)
def test_a_bad_answer_is_refused(decide, arg, error):
    r = decide("--answer", arg)
    assert r.returncode != 0
    assert error in r.stderr


def test_answers_to_questions_no_longer_asked_are_reported(cache, checked, decide):
    decide("--answer", "payments=yes")
    # made again (bindings changed), with other questions
    put_assessment(cache, fixture_assessment(asked=False))
    out = checked()
    assert out["stale_answers"] == {
        "payments": "Does this code deploy into a payments account?"
    }
    assert cid("PCI-DSS AWS") not in out["in_scope"]


def test_without_an_assessment_only_all_accounts_collections_apply(ingested, run):
    # none made yet: a sync makes it (assess.py show); guardrails may be missing meanwhile
    r = run("distill.py", "index")
    assert "warning: no posture assessment yet" in r.stderr
    assert "[sync: fetch]" in r.stderr
    r = run("scope.py", "check")
    out = json.loads(r.stdout)
    assert out["assessment"].startswith("none made yet")
    assert sorted(c["name"] for c in out["in_scope"].values()) == [
        "Acme Org Standards",
        "CIS AWS Baseline",
    ]
    assert out["unanswered_questions"] == {}
    assert r.returncode == 0
    r = run("scope.py", "decide", "--answer", "payments=yes", "--because", "t")
    assert r.returncode != 0
    assert "assess.py show makes the assessment" in r.stderr


def test_the_scope_file_is_found_up_to_the_repo_root_and_no_further(tmp_path):
    # outside a repo, an ancestor's scope file (anyone's, under /tmp) must not choose the scope
    planted = tmp_path / scope.SCOPE_FILE
    planted.parent.mkdir(parents=True)
    planted.write_text("{}")
    work = tmp_path / "work" / "sub"
    work.mkdir(parents=True)
    assert scope.default_scope_file(work) == work / scope.SCOPE_FILE
    # inside a repo: from a subdirectory, the root's, even before it exists
    (tmp_path / "work" / ".git").mkdir()
    assert scope.default_scope_file(work) == tmp_path / "work" / scope.SCOPE_FILE


def test_the_report_lists_collections_in_name_order(run, cache, pristine_fx, tmp_path):
    # posture only: nothing loaded, so everything in scope is unloaded too
    ingest_posture_only(run, pristine_fx, tmp_path)
    put_assessment(cache, fixture_assessment())
    out = json.loads(run("scope.py", "check").stdout)
    assert list(out["in_scope"].values()) == [
        {"name": "Acme Org Standards", "loaded": False, "checked": False},
        {"name": "CIS AWS Baseline", "loaded": False, "checked": False},
    ]
    q = out["unanswered_questions"]["payments"]
    assert q["resource_types"] == {}
    assert q["resource_types_complete"] is False


def test_an_account_s_tags_are_reported_as_written(look_up, checked, decide):
    # for questions about tagged accounts; as written, in order, a key may appear twice
    tags = [
        {"key": "team", "value": "payments"},
        {"key": "label", "value": "env:dev"},
        {"key": "label", "value": "tier:1"},
        {"key": "GuardrailPause", "value": None},
    ]
    look_up(account_response("111", ["All aws accounts"], tags=tags))
    look_up(account_response("222", ["All aws accounts"]))  # none: null
    decide("--account", "AWS:111", "AWS:222")
    accounts = checked()["accounts"]
    assert accounts["AWS:111"]["tags"] == [
        "team=payments",
        "label=env:dev",
        "label=tier:1",
        "GuardrailPause",
    ]
    assert accounts["AWS:222"]["tags"] == []


def test_a_guardrails_db_of_another_layout_is_deleted_and_started_afresh(
    ingested, cache, run, look_up, decide
):
    path = cache / FIXTURE_DEPLOYMENT / "guardrails.db"
    db = sqlite3.connect(path)
    with db:
        db.execute("DROP TABLE account")  # another layout: no account.tags
        db.execute(
            "CREATE TABLE account (provider TEXT, key TEXT, name, path, org_id, active INT,"
            " groups JSON, looked_up TEXT, PRIMARY KEY (provider, key))"
        )
        db.execute("PRAGMA user_version = 0")
    db.close()
    journal = path.with_name("guardrails.db-journal")
    journal.write_bytes(b"left behind")  # must not be applied to the new db
    r = run("distill.py", "index")
    assert "deleted" in r.stderr
    assert not journal.exists()
    assert not path.with_name("guardrails.db.old").exists()
    look_up(
        account_response("222", ["All aws accounts"], tags=[{"key": "a", "value": "b"}])
    )
    db = sqlite3.connect(path)
    assert db.execute("SELECT count(*) FROM policy").fetchone() == (0,)  # fetched again
    assert db.execute("SELECT tags FROM account").fetchone() == (
        '[{"key": "a", "value": "b"}]',
    )
    # and only once
    assert "deleted" not in run("distill.py", "index").stderr


def test_a_guardrails_db_that_is_no_database_is_deleted_too(ingested, cache, run):
    path = cache / FIXTURE_DEPLOYMENT / "guardrails.db"
    path.write_bytes(b"not a database SQLite can read" * 100)
    r = run("distill.py", "index")
    assert "Traceback" not in r.stderr
    assert "deleted" in r.stderr


def test_one_that_cannot_be_removed_is_said_not_a_traceback(
    ingested, cache, open_cache, monkeypatch
):
    # Windows refuses to delete a file another process has open
    path = cache / FIXTURE_DEPLOYMENT / "guardrails.db"
    db = sqlite3.connect(path)
    with db:
        db.execute("PRAGMA user_version = 0")
    db.close()

    def refuse(self, missing_ok=False):
        raise PermissionError("in use")

    monkeypatch.setattr(_cache.Path, "unlink", refuse)
    with pytest.raises(SystemExit, match="can't be removed .*close other sessions"):
        open_cache()


def test_without_an_assessment_answers_are_kept_not_stale(cache, checked, decide):
    # a model failure says nothing about them: the agent must not forget them
    decide("--answer", "payments=yes")
    db = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "guardrails.db")
    with db:
        db.execute("DELETE FROM assessment")
    out = checked()
    assert out["assessment"].startswith("none made yet")
    assert out["stale_answers"] == {}
    assert out["answers"] == {"payments": "yes"}


def reword_payments(cache):
    """Assess again, with the payments question's id given to another question."""
    other = fixture_assessment()
    other["questions"][0]["ask"] = "Does this code deploy into a sandbox?"
    put_assessment(cache, other)


def test_an_id_reused_for_another_question_does_not_take_its_answer(
    cache, checked, decide
):
    decide("--answer", "payments=yes")
    reword_payments(cache)
    out = checked()
    assert cid("PCI-DSS AWS") not in out["in_scope"]
    assert "payments" in out["unanswered_questions"]
    assert out["stale_answers"] == {
        "payments": "Does this code deploy into a payments account?"
    }


def test_an_opt_out_is_not_asked_when_a_yes_could_remove_nothing(
    look_up, checked, decide
):
    # what an account reaches stays whatever the answer: the account is in the group bound to
    # the rollout, so "only into exempt accounts?" can't take it away
    look_up(account_response("111", ["All aws accounts", "aws-not-exempt"]))
    decide("--account", "AWS:111")
    assert "only-exempt" not in checked()["unanswered_questions"]


def test_nor_when_what_it_removes_is_included(checked, decide):
    decide("--include", cid(ROLLOUT))
    assert "only-exempt" not in checked()["unanswered_questions"]


def test_nor_a_question_whose_adds_are_all_excluded(checked, decide):
    decide("--exclude", cid("PCI-DSS AWS"))
    assert "payments" not in checked()["unanswered_questions"]


def test_a_later_decide_keeps_a_stale_answer_stale(cache, checked, decide):
    # refreshing its text from the reused id's new question would hand it that question
    decide("--answer", "payments=yes")
    reword_payments(cache)
    # anything, so the file is written again
    decide("--include", cid("Sandbox Tagging"))
    out = checked()
    assert out["stale_answers"] == {
        "payments": "Does this code deploy into a payments account?"
    }
    assert "payments" in out["unanswered_questions"]
    # answering the new question replaces the stale answer under the same id
    decide("--answer", "payments=no")
    out = checked()
    assert out["stale_answers"] == {}
    assert out["answers"] == {"payments": "no"}


def test_an_answer_switching_off_what_applies_is_surfaced(run, checked, decide):
    decide("--answer", "only-exempt=yes")
    out = checked()
    assert out["removed_by_answers"] == [
        {
            "collection": cid(ROLLOUT),
            "name": ROLLOUT,
            "question": "only-exempt",
            "because": "t",
        }
    ]
    r = run("distill.py", "index")
    assert f"answer to 'only-exempt' removes collection '{ROLLOUT}'" in r.stderr


def test_an_answer_without_its_question_text_makes_the_file_invalid(
    checked, scope_file
):
    scope_file.parent.mkdir(parents=True)
    scope_file.write_text(
        json.dumps(
            {"version": 5, "answers": [{"question": "only-exempt", "answer": "yes"}]}
        )
    )
    out = checked()
    assert out["scope_file_invalid"]
    assert ROLLOUT in [c["name"] for c in out["in_scope"].values()]


def test_an_unreadable_guardrails_db_is_said_not_deleted(ingested, cache, run):
    path = cache / FIXTURE_DEPLOYMENT / "guardrails.db"
    if os.geteuid() == 0:
        pytest.skip("root reads anything")
    path.chmod(0)
    try:
        r = run("distill.py", "index")
    finally:
        path.chmod(0o600)
    assert r.returncode != 0
    assert "cannot read the guardrail cache" in r.stderr
    assert path.exists()


def test_a_new_guardrails_db_is_stamped_with_its_tables(open_cache):
    # in one transaction: a session seeing tables without the stamp would delete the db
    db = open_cache(posture=False)
    assert db.execute("PRAGMA user_version").fetchone()[0] == _cache.GUARDRAILS_LAYOUT
    assert db.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] > 0


def test_a_guardrails_db_of_a_newer_layout_is_left_alone(ingested, cache, run):
    # a session running a newer version of the skill made it: deleting it would have each version
    # delete the other's on every open
    path = cache / FIXTURE_DEPLOYMENT / "guardrails.db"
    db = sqlite3.connect(path)
    with db:
        db.execute(f"PRAGMA user_version = {_cache.GUARDRAILS_LAYOUT + 1}")
    db.close()
    before = path.read_bytes()
    r = run("distill.py", "index")
    assert r.returncode != 0
    assert "made by a newer version of the skill: update the skill" in r.stderr
    assert path.read_bytes() == before


# The guardrails db's schema as of GUARDRAILS_LAYOUT; see the test below.
GUARDRAILS_SCHEMA_SHA = {3: "935ad5f1bc1b9c4b"}


def test_a_guardrails_layout_change_bumps_the_stamp():
    # a changed GUARDRAILS_SCHEMA with an unchanged GUARDRAILS_LAYOUT would let an old db through,
    # its tables lacking what the code now reads (CREATE TABLE IF NOT EXISTS adds no column): bump
    # _cache.GUARDRAILS_LAYOUT, then record the new hash here. A change that only adds a table needs
    # no bump (it is created on open, in an old db too): record its hash under the same layout.
    sha = hashlib.sha256(_cache.GUARDRAILS_SCHEMA.encode()).hexdigest()[:16]
    assert GUARDRAILS_SCHEMA_SHA.get(_cache.GUARDRAILS_LAYOUT) == sha, (
        "_cache.GUARDRAILS_SCHEMA changed: bump _cache.GUARDRAILS_LAYOUT and set "
        f"GUARDRAILS_SCHEMA_SHA[{_cache.GUARDRAILS_LAYOUT}] = {sha!r}"
    )


def test_a_rule_s_verdict_follows_the_posture_not_the_cache(ingest, fx, cache, checked):
    # bound to every account while still empty (ignored by rule), it later gains policies: the
    # binding set and so the cached assessment are unchanged, but it applies now
    add_empty_collection(fx)
    assert ingest(fx).returncode == 0
    put_assessment(cache, fixture_assessment())
    assert "uuid-empty" not in checked()["in_scope"]

    def fill(b):
        c = next(c for c in b["policyCollections"] if c["uuid"] == "uuid-empty")
        c["policyMappings"] = b["policyCollections"][0]["policyMappings"]

    edit_posture(fx, fill)
    assert ingest(fx).returncode == 0
    assert "uuid-empty" in checked()["in_scope"]


def test_show_names_the_questions_that_bear_on_its_type(assessed, run):
    # payments' collection has guardrails for aws.s3 and aws.rds, none for aws.ec2
    r = run("distill.py", "show", "aws.s3", "--dry-run")
    assert (
        "unanswered question payments could change the guardrails for aws.s3"
        in r.stderr
    )
    r = run("distill.py", "show", "aws.ec2", "--dry-run")
    assert "question payments" not in r.stderr
    # index raises none: which a change touches is known type by type
    assert "[question]" not in run("distill.py", "index").stderr


def test_a_question_is_not_asked_until_what_it_bears_on_is_known(run, fx, cache):
    # its collections not loaded: a sync, not a question
    edit_posture(fx, add_opt_out)
    assert run("ingest.py", "posture", fx / "posture.json").returncode == 0
    put_assessment(cache, fixture_assessment())
    r = run("distill.py", "show", "aws.s3", "--dry-run")
    assert "[question]" not in r.stderr
    assert "question(s) payments, only-exempt bear on isn't known" in r.stderr
    assert "[sync: fetch]" in r.stderr
    # loaded, but an account not looked up yet, whose lookup may settle it
    assert run("ingest.py", "collections", fx / "collections.json").returncode == 0
    assert (
        "question payments" in run("distill.py", "show", "aws.s3", "--dry-run").stderr
    )
    r = run("scope.py", "decide", "--account", "AWS:999999999999", "--because", "t")
    assert r.returncode == 0
    r = run("distill.py", "show", "aws.s3", "--dry-run")
    # still asked (nothing may ever look it up: no server, say), but said to be pending one
    assert "question payments" in r.stderr
    assert (
        "AWS:999999999999 aren't looked up yet, and a lookup may settle it" in r.stderr
    )


def test_a_question_reworded_names_the_answer_recorded(assessed, run, decide, cache):
    assert decide("--answer", "payments=no").returncode == 0
    a = fixture_assessment()
    a["questions"] = [
        {**q, "ask": q["ask"] + " (reworded)"} if q["id"] == "payments" else q
        for q in a["questions"]
    ]
    put_assessment(cache, a)
    r = run("distill.py", "show", "aws.s3", "--dry-run")
    assert "an answer (no) to its earlier wording is recorded" in r.stderr


def test_an_ignored_scope_file_raises_no_questions(assessed, run, scope_file):
    scope_file.parent.mkdir(parents=True)
    scope_file.write_text("{not json")
    r = run("distill.py", "show", "aws.s3", "--dry-run")
    assert "[tell the user]" in r.stderr
    assert "[question]" not in r.stderr


def test_query_fetches_the_scope_and_what_its_questions_name(run, fx, cache, tmp_path):
    edit_posture(fx, add_opt_out)
    assert run("ingest.py", "posture", fx / "posture.json").returncode == 0
    put_assessment(cache, fixture_assessment())
    q = run("scope.py", "query").stdout
    # in scope, and the payments question's collection
    for name in ("CIS AWS Baseline", "Acme Org Standards", ROLLOUT, "PCI-DSS AWS"):
        assert f'"{cid(name)}"' in q, name
    # the fixture's response lacks the rollout (add_opt_out's): only it is still due
    assert run("ingest.py", "collections", fx / "collections.json").returncode == 0
    q = run("scope.py", "query").stdout
    assert f'"{cid(ROLLOUT)}"' in q
    assert cid("CIS AWS Baseline") not in q


def test_repo_reads_another_directorys_scope_file(decide, run, tmp_path, repo):
    assert decide("--include", cid("Sandbox Tagging")).returncode == 0
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = run("scope.py", "check", "--repo", repo, cwd=elsewhere)
    assert r.stdout, r.stderr
    out = json.loads(r.stdout)
    assert out["scope_file"] == (repo / ".stacklet/guardrails/scope.json").as_posix()
    assert cid("Sandbox Tagging") in out["in_scope"]
    # without it, the working directory's (none)
    out = json.loads(run("scope.py", "check", cwd=elsewhere).stdout)
    assert out["scope_file"] is None
    r = run("distill.py", "show", "aws.ec2", "--dry-run", "--repo", repo, cwd=elsewhere)
    assert "Sandbox Tagging" in r.stdout
    r = run("distill.py", "show", "aws.ec2", "--dry-run", cwd=elsewhere)
    assert "Sandbox Tagging" not in r.stdout


def test_check_never_makes_an_assessment(opted_out, run, tmp_path):
    # only assess.py show does (a sync's scope step): a sync tries at most once, and checking (the
    # agent's, while a sync runs) never makes a second beside it
    marker = tmp_path / "called"
    env = {"STACKLET_GUARDRAILS_INFERENCE_CMD": json.dumps(["touch", str(marker)])}
    out = json.loads(run("scope.py", "check", **env).stdout)
    assert out["assessment"].startswith("none made yet")
    assert not marker.exists()
    run("assess.py", "show", **env)
    assert marker.exists()


def test_an_earlier_assessment_stands_in_while_a_new_one_is_made(
    assessed, run, decide, fx
):
    assert decide("--answer", "payments=yes").returncode == 0
    assert cid("PCI-DSS AWS") in json.loads(run("scope.py", "check").stdout)["in_scope"]

    # the bindings change: the assessment made for the old ones is the newest there is
    def rebind(b):
        b["bindings"].append({**b["bindings"][0], "uuid": "b-new", "name": "new"})

    edit_posture(fx, rebind)
    assert run("ingest.py", "posture", fx / "posture.json").returncode == 0
    r = run("distill.py", "show", "aws.rds", "--dry-run")
    # not the all-accounts defaults, which would drop what the answer brought in
    assert "PCI-DSS AWS" in r.stdout
    assert "made for earlier bindings, and stands in" in r.stderr
    assert "[sync: fetch]" in r.stderr
    assert (
        "[question]" not in r.stderr
    )  # its questions can't be answered: decide takes the new one's
    r = run("scope.py", "check")
    out = json.loads(r.stdout)
    assert cid("PCI-DSS AWS") in out["in_scope"]
    assert "for earlier bindings" in out["assessment"]
    # its questions held back, as show holds them: decide couldn't record an answer to them
    assert out["unanswered_questions"] == {}
    assert r.returncode == 0
    r = run("scope.py", "decide", "--answer", "only-exempt=no", "--because", "t")
    assert r.returncode != 0
    assert "the cached one is for earlier bindings" in r.stderr
    # and when making one fails (the tests' command), it still stands in
    assert run("assess.py", "show").returncode != 0
    assert cid("PCI-DSS AWS") in json.loads(run("scope.py", "check").stdout)["in_scope"]
