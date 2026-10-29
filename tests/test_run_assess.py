"""The posture assessment's evals: the committed postures, the grader and the runner, with no model
call."""

import json
import re
import shutil
import subprocess
import sys

import grade_assess
import pytest
import run_assess


EVALS = run_assess.evals()


@pytest.mark.parametrize("e", EVALS, ids=[e["key"] for e in EVALS])
def test_each_committed_posture_ingests_and_its_expectations_name_its_collections(
    e, run
):
    posture = json.loads(e["posture"].read_text(encoding="utf-8"))
    dep = run_assess.deployment(posture)
    r = run("ingest.py", "posture", e["posture"], deployment=dep)
    assert r.returncode == 0, r.stderr
    console = posture["data"]["platform"]["urls"]["console"]
    assert re.fullmatch(r"https://console\.assess-\d+\.example/", console)
    names = {c["name"] for c in posture["data"]["bindingPosture"]["policyCollections"]}
    expect = e.get("expect") or {}
    assert set(expect.get("collections") or {}) <= names
    for q in expect.get("questions") or []:
        assert set(q.get("adds") or q.get("removes")) <= names


def test_the_committed_postures_are_of_distinct_deployments():
    deps = [
        run_assess.deployment(json.loads(e["posture"].read_text(encoding="utf-8")))
        for e in EVALS
    ]
    assert len(set(deps)) == len(deps)


def posture(groups, bindings):
    """A posture response: GROUPS (uuid, filter, size), and BINDINGS (group uuid, collection name),
    every collection AWS's with one policy."""
    names = sorted({c for _, c in bindings})
    return {
        "data": {
            "platform": {"urls": {"console": "https://console.t.example/"}},
            "cloudOrganizations": {"edges": [{"node": {"key": "o-1"}}]},
            "bindingPosture": {
                "accountGroups": [
                    {
                        "uuid": u,
                        "name": u,
                        "provider": "AWS",
                        "dynamicFilter": f,
                        "accountMappings": {"pageInfo": {"total": n}},
                    }
                    for u, f, n in groups
                ],
                "policyCollections": [
                    {
                        "uuid": f"c-{n}",
                        "name": n,
                        "provider": "AWS",
                        "policyMappings": {"pageInfo": {"total": 1}},
                    }
                    for n in names
                ],
                "bindings": [
                    {
                        "lastDeployed": "t",
                        "accountGroup": {"uuid": g},
                        "policyCollection": {"uuid": f"c-{c}"},
                    }
                    for g, c in bindings
                ],
            },
        }
    }


P = grade_assess.Posture(
    posture(
        [("all", "", 100), ("big", "tag:a=b", 60), ("few", None, 3)],
        [("all", "everywhere"), ("big", "most"), ("few", "handful")],
    )
)


def result(questions=None, **defaults):
    return {
        "assessment": {
            "collections": {
                f"c-{n}": {"default": d, "why": "w"} for n, d in defaults.items()
            },
            "questions": questions
            if questions is not None
            else [
                {
                    "id": "q",
                    "ask": "Q?",
                    "into": ["big"],
                    "add": ["c-most"],
                    "remove": [],
                }
            ],
            "notes": [],
        }
    }


def checks(r, expect=None, p=P):
    return dict(grade_assess.run_checks(r, p, expect or {}))


def test_a_sound_assessment_passes():
    got = checks(result(everywhere="applies", most="ask", handful="ignore"))
    assert all(got.values()), got


def test_ignoring_what_reaches_most_accounts_fails():
    got = checks(result(everywhere="applies", most="ignore", handful="ignore"))
    assert not got["Nothing broad (a whole org, or >= 50%) ignored: most"]


def test_applying_what_reaches_a_handful_fails():
    got = checks(result(everywhere="applies", most="ask", handful="applies"))
    assert not got["Nothing narrow (< 25%) applies: handful"]


def test_expectations_are_checked():
    expect = {
        "collections": {"most": ["applies"]},
        "questions": [{"adds": ["most"]}, {"removes": ["handful"]}],
    }
    got = checks(result(everywhere="applies", most="ask", handful="ignore"), expect)
    assert got["most: applies"] is False
    assert got["Some question's yes adds most"] is True
    assert got["Some question's yes removes handful"] is False


# an org of 100 accounts: two OUs (one split across two groups by tag), and a group of two OUs
ORG = posture(
    [
        ("all", "", 100),
        ("org", '{"single":{"name":"org","operator":"equals","values":["o-1"]}}', 100),
        ("ou-a", "org:o-1 path:/A/", 60),
        ("ou-a-tagged", "org:o-1 tag:x=y path:/A/", 20),
        ("ou-b", "org:o-1 path:/B/", 37),
        (
            "a-or-b",
            json.dumps(
                {
                    "multiple": {
                        "operator": "AND",
                        "operands": [
                            {
                                "single": {
                                    "name": "org",
                                    "operator": "equals",
                                    "value": "o-1",
                                }
                            },
                            {
                                "multiple": {
                                    "operator": "OR",
                                    "operands": [
                                        {
                                            "single": {
                                                "name": "path",
                                                "operator": "starts with",
                                                "value": v,
                                            }
                                        }
                                        for v in ("/A/", "/B/")
                                    ],
                                }
                            },
                        ],
                    }
                }
            ),
            97,
        ),
    ],
    [
        ("org", "org-wide"),
        ("ou-a", "a"),
        ("ou-a-tagged", "a-tagged"),
        ("ou-b", "b"),
        ("ou-b", "b-too"),
        ("a-or-b", "a-and-b"),
    ],
)


def test_which_groups_hold_which_follows_org_and_ou_filters():
    p = grade_assess.Posture(ORG)
    # of the bound groups ("all" carries nothing here)
    assert p.within["ou-a-tagged"] == {"org", "ou-a", "ou-a-tagged", "a-or-b"}
    assert p.within["ou-b"] == {"org", "ou-b", "a-or-b"}
    assert p.within["a-or-b"] == {"org", "a-or-b"}
    # OUs apart: their sizes add up; nested ones don't
    assert p.surely["c-a-and-b"] == 0.97
    assert grade_assess.disjoint(
        grade_assess.region("org:o-1 path:/A/"), grade_assess.region("org:o-1 path:/B/")
    )


def test_asking_about_what_reaches_nearly_every_account_fails():
    p = grade_assess.Posture(ORG)
    got = checks(result([], **{"org-wide": "ask"}), p=p)
    assert not got["Nothing reaching >= 95% only asked about: org-wide"]
    # 97% by OU groups that can't overlap: surely that much
    got = checks(result([], **{"a-and-b": "ask"}), p=p)
    assert not got["Nothing reaching >= 95% only asked about: a-and-b"]


def assessed(questions, **defaults):
    """An assessment of ORG: QUESTIONS as (id, into), each adding what its groups carry."""
    p = grade_assess.Posture(ORG)
    return result(
        [
            {
                "id": i,
                "ask": f"{i}?",
                "into": into,
                "add": sorted({u for g in into for u in p.on[g]}),
                "remove": [],
            }
            for i, into in questions
        ],
        **defaults,
    )


ASKED = {n: "ask" for n in ("a", "a-tagged", "b", "b-too", "a-and-b")}


def test_a_question_joining_groups_of_different_collections_is_imprecise():
    p = grade_assess.Posture(ORG)
    good = assessed(
        [
            ("a", ["ou-a"]),
            ("tagged", ["ou-a-tagged"]),
            ("b", ["ou-b"]),
            ("ab", ["a-or-b"]),
        ],
        **{"org-wide": "applies"},
        **ASKED,
    )
    assert grade_assess.run_measures(good, p) == []
    # "A or B": a developer in A is given B's guardrails
    joined = assessed(
        [("a-or-b", ["ou-a", "ou-b", "ou-a-tagged", "a-or-b"])],
        **{"org-wide": "applies"},
        **ASKED,
    )
    m = grade_assess.run_measures(joined, p)[0]
    # B's developers (37% of accounts) are given A's
    assert m.startswith(
        "imprecise: a yes adds collections bound to none of their groups for "
        "developers in groups of up to 37% of accounts, e.g. a, a-tagged"
    )


def test_runs_agree_on_scope_however_their_questions_are_grouped():
    p = grade_assess.Posture(ORG)
    split = assessed(
        [
            ("b1", ["ou-b"]),
            ("a", ["ou-a"]),
            ("tagged", ["ou-a-tagged"]),
            ("ab", ["a-or-b"]),
        ],
        **{"org-wide": "applies"},
        **ASKED,
    )
    reworded = json.loads(json.dumps(split))
    for q in reworded["assessment"]["questions"]:
        q["id"], q["ask"] = q["id"] + "-x", "Reworded?"
    (check,), _ = grade_assess.stable([split, reworded], p)
    assert check[1], check
    # one run gives B's developers nothing: the share of accounts that differ is shown
    missed = assessed(
        [("a", ["ou-a"]), ("tagged", ["ou-a-tagged"]), ("ab", ["a-or-b"])],
        **{"org-wide": "applies"},
        **ASKED,
    )
    (check,), _ = grade_assess.stable([split, split, missed], p)
    assert "(94%); differs on b, b-too" in check[0]
    assert not grade_assess.kept(split, reworded)[0][1]


def test_runs_disagreeing_on_most_accounts_fail():
    # one run adds what reaches 60% of accounts by a question, the other by nothing at all
    asked = result(everywhere="applies", most="ask", handful="ignore")
    unasked = result([], everywhere="applies", most="ignore", handful="ignore")
    (check,), measures = grade_assess.stable([asked, unasked], P)
    assert not check[1], check
    assert measures == ["defaults differ between runs on: most"]


def stub_model(tmp_path):
    """An inference command that says every collection in the digest applies."""
    stub = tmp_path / "stub.py"
    stub.write_text(
        "import json, re, sys\n"
        "p = sys.stdin.read()\n"
        "names = re.findall(r'^  - (.+?): \\d+ polic', p, re.M)\n"
        "print(json.dumps({'collections': [\n"
        "    {'name': n, 'default': 'applies', 'why': 'w'} for n in names]}))\n",
        encoding="utf-8",
    )
    return json.dumps([sys.executable, str(stub)])


def run_tool(name, *args):
    """Run run_assess.py or grade_assess.py with ARGS."""
    return subprocess.run(
        [sys.executable, str(run_assess.HERE / name), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_runner_and_grader_end_to_end_with_a_stub(tmp_path, monkeypatch):
    # every collection applies, as the stub model says; eval 8 needs no model at all
    monkeypatch.setenv("STACKLET_GUARDRAILS_INFERENCE_CMD", stub_model(tmp_path))
    out = tmp_path / "out"
    r = run_tool("run_assess.py", out, "--runs", "2", "--evals", "3,8", "--yes")
    assert r.returncode == 0, r.stderr
    for key in ("03-poc-bound-everywhere", "08-dashboards-only"):
        assert (out / key / "run-2" / "result.json").exists()
        assert (out / key / "reassess" / "result.json").exists()
    rules_only = json.loads(
        (out / "08-dashboards-only" / "run-1" / "result.json").read_text()
    )
    # rules decided everything
    assert rules_only["inference_cmd"] == {"argv": [], "custom": False}
    g = run_tool("grade_assess.py", out)
    assert g.returncode == 0, g.stderr
    assert "08-dashboards-only" in g.stdout
    # applying everything applies what reaches a handful: the grader says so
    assert "Nothing narrow (< 25%) applies" in g.stdout


def test_a_coverage_gap_counts_only_the_accounts_left_out():
    # "a-or-b" (97%) carries a-and-b, added by the questions about A and B but asked about by no
    # question of its own: its developers in neither A nor B (none here) are the only ones left out
    p = grade_assess.Posture(ORG)
    by_parts = assessed(
        [
            ("a", ["ou-a", "a-or-b"]),
            ("tagged", ["ou-a-tagged"]),
            ("b", ["ou-b", "a-or-b"]),
        ],
        **{"org-wide": "applies"},
        **ASKED,
    )
    assert not any(
        m.startswith("coverage") for m in grade_assess.run_measures(by_parts, p)
    )
    # with B's question gone, B's 37% are left without B's own
    no_b = assessed(
        [("a", ["ou-a"]), ("tagged", ["ou-a-tagged"]), ("ab", ["a-or-b"])],
        **{"org-wide": "applies"},
        **ASKED,
    )
    gaps = [m for m in grade_assess.run_measures(no_b, p) if m.startswith("coverage")]
    assert gaps == [
        (
            "coverage gap: no yes adds these 'ask' collections for developers in up to 37% of "
            "accounts: b, b-too"
        )
    ]


def test_runs_disagreeing_only_on_a_narrow_groups_questions_fail():
    # all accounts and "big" get what applies in every run, so only "few" (3%) counts: weighted
    # among every group, its disagreement would be lost in theirs
    asked = result(
        [{"id": "q", "ask": "Q?", "into": ["few"], "add": ["c-handful"], "remove": []}],
        everywhere="applies",
        most="ask",
        handful="ask",
    )
    unasked = result([], everywhere="applies", most="ask", handful="ask")
    (check,), _ = grade_assess.stable([asked, unasked], P)
    assert not check[1], check
    assert "(50%); differs on handful" in check[0]
    # the same scope by differently worded questions agrees
    reworded = json.loads(json.dumps(asked))
    reworded["assessment"]["questions"][0].update(id="other", ask="Into the few?")
    (check,), _ = grade_assess.stable([asked, reworded], P)
    assert check == ("Runs agree on developers' scope >= 90% (100%)", True)
    # no group whose scope questions decide: nothing to disagree on
    (check,), _ = grade_assess.stable([unasked, unasked], P)
    assert check[1], check


def test_a_run_without_an_assessment_is_left_out_across_runs():
    failed = {"rc": 1, "err": "boom"}
    good = result(everywhere="applies", most="ask", handful="ignore")
    good["stats"] = {
        "seconds": 10,
        "input_tokens": 90,
        "output_tokens": 10,
        "cost_usd": 0.1,
    }
    assert checks(failed) == {"Assessment made": False}
    assert grade_assess.run_measures(failed, P) == []
    (check,), _ = grade_assess.stable([failed, good, good], P)
    assert check[1], check
    assert grade_assess.stable([failed, good], P) == ([], [])
    assert grade_assess.took([failed, good]) == "10-10s, ~100 tokens, $0.10"
    assert grade_assess.took([failed]) == "no runs made"
    # nothing to keep of a run that made nothing
    assert grade_assess.kept(failed, good) == []


def test_a_failed_reassessment_fails():
    good = result(everywhere="applies", most="ask", handful="ignore")
    for again in ({"rc": 1, "err": "boom"}, {"rc": 0, "err": ""}):
        assert grade_assess.kept(good, again) == [
            (f"Reassessment made (rc={again['rc']})", False)
        ]
    # a failed run keeps no assessment, even one copied from the run it reassessed
    assert grade_assess.kept(good, {**good, "rc": 1}) == [
        ("Reassessment made (rc=1)", False)
    ]
    assert grade_assess.kept(good, {**good, "rc": 0}) == [
        ("Reassessment kept every question (1/1)", True)
    ]


def test_a_question_naming_a_part_of_a_group_is_not_a_yes_for_the_whole():
    p = grade_assess.Posture(ORG)
    a = result(
        [
            {
                "id": "q",
                "ask": "Q?",
                "into": ["ou-a", "ou-a-tagged"],
                "add": ["c-a-tagged"],
                "remove": [],
            }
        ],
        **{"org-wide": "applies"},
        **ASKED,
    )["assessment"]
    # it asks about the tagged part of A, which a developer in A may not deploy into
    assert "c-a-tagged" not in p.scope(a, "ou-a")
    assert "c-a-tagged" in p.scope(a, "ou-a-tagged")
    # naming A alone, it asks about all of A
    a["questions"][0]["into"] = ["ou-a"]
    assert "c-a-tagged" in p.scope(a, "ou-a")


def test_runs_are_graded_in_numeric_order(tmp_path, capsys):
    out = tmp_path / "out"
    key = "01-x"
    for k in range(1, 11):
        d = out / key / f"run-{k}"
        d.mkdir(parents=True)
        (d / "posture.json").write_text(
            json.dumps(posture([("all", "", 100)], [("all", "everywhere")])),
            encoding="utf-8",
        )
        # only run 10 failed: read lexically, it would be the second
        r = {"rc": 1} if k == 10 else result([], everywhere="applies")
        (d / "result.json").write_text(json.dumps(r), encoding="utf-8")
    (out / "evals.json").write_text(json.dumps([{"key": key}]), encoding="utf-8")
    grade_assess.main(out)
    printed = capsys.readouterr().out
    assert "FAIL run 10: Assessment made" in printed
    assert "FAIL run 2:" not in printed


def test_evals_json_keeps_the_evals_of_earlier_batches(tmp_path):
    three, five = (e for e in EVALS if e["id"] in (3, 5))
    run_assess.record(tmp_path, [three, five])
    stale = json.loads((tmp_path / "evals.json").read_text(encoding="utf-8"))
    stale[0]["shape"] = "stale"
    (tmp_path / "evals.json").write_text(json.dumps(stale), encoding="utf-8")
    # resuming with eval 5 alone keeps eval 3, and updates what it runs
    run_assess.record(tmp_path, [{**five, "shape": "new"}])
    got = json.loads((tmp_path / "evals.json").read_text(encoding="utf-8"))
    assert [(e["key"], e["shape"]) for e in got] == [
        (three["key"], "stale"),
        (five["key"], "new"),
    ]


def test_a_failed_reassessment_is_recorded_and_a_remade_first_run_remakes_it(
    tmp_path, monkeypatch
):
    good = stub_model(tmp_path)
    out = tmp_path / "out"
    key = "03-poc-bound-everywhere"

    def batch(cmd):
        monkeypatch.setenv("STACKLET_GUARDRAILS_INFERENCE_CMD", cmd)
        r = run_tool("run_assess.py", out, "--runs", "1", "--evals", "3", "--yes")
        assert r.returncode == 0, r.stderr
        return json.loads(
            (out / key / "reassess" / "result.json").read_text(encoding="utf-8")
        )

    assert batch(good)["assessment"]
    # the reassessment fails: run 1's copied assessment is not taken for its own
    shutil.rmtree(out / key / "reassess")
    failed = batch(json.dumps(["false"]))
    assert failed["rc"]
    assert "assessment" not in failed
    g = run_tool("grade_assess.py", out)
    assert g.returncode == 0, g.stderr
    assert f"FAIL Reassessment made (rc={failed['rc']})" in g.stdout
    # run 1 remade: so is the reassessment of it, not skipped as done
    (out / key / "run-1" / "result.json").unlink()
    assert batch(good)["assessment"]


def test_an_unknown_eval_id_is_refused(tmp_path):
    # rather than a batch that silently runs nothing
    r = run_tool("run_assess.py", tmp_path / "out", "--evals", "3,99")
    assert r.returncode != 0
    assert "no such eval: 99" in r.stderr


def test_the_reassessment_is_checked_against_run_1_only(tmp_path, capsys):
    # run 1 deleted (to be made again): its reassessment is checked against nothing, never run 2,
    # and run 2 is still called run 2
    out = tmp_path / "out"
    key = "01-x"
    d = out / key / "run-2"
    d.mkdir(parents=True)
    (d / "posture.json").write_text(
        json.dumps(posture([("all", "", 100)], [("all", "everywhere")])),
        encoding="utf-8",
    )
    (d / "result.json").write_text(
        json.dumps(result(everywhere="applies")), encoding="utf-8"
    )
    (out / key / "reassess").mkdir()
    (out / key / "reassess" / "result.json").write_text(
        json.dumps(result([], everywhere="applies")), encoding="utf-8"
    )
    (out / "evals.json").write_text(json.dumps([{"key": key}]), encoding="utf-8")
    grade_assess.main(out)
    graded = json.loads((out / key / "grading.json").read_text(encoding="utf-8"))
    texts = [c["text"] for c in graded["checks"]]
    assert not [t for t in texts if "Reassessment" in t]
    assert texts[0].startswith("run 2: ")
