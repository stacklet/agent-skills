"""The distillation evals' runner and grader (tests/evals/run_distill.py, grade_distill.py), and
each committed eval's controls: no model calls."""

import json
import re
import sys

import grade_distill
import pytest
import run_distill

from distill import clean_distillation


EVALS = run_distill.evals()


def test_there_are_evals():
    assert len(EVALS) >= 10


@pytest.mark.parametrize("e", EVALS, ids=lambda e: e["key"])
def test_an_eval_is_well_formed(e):
    names = [p["name"] for p in e["policies"]]
    assert len(set(names)) == len(names)
    assert all(p["source"]["resource"] == e["resource_type"] for p in e["policies"])
    ids = [x["id"] for x in e["expect"]]
    assert len(set(ids)) == len(ids)
    assert not set(ids) & set(grade_distill.GENERAL)
    for x in e["expect"]:
        assert set(x["policies"]) <= set(names), x["id"]
    # the prompt distill.py would send names every policy
    prompt = run_distill.prompt_of(e)
    assert all(f"{p['name']}@{p['version']}" in prompt for p in e["policies"])
    for x in e["expect"]:
        for pat in (*x.get("says", []), *x.get("not", [])):
            re.compile(pat)
    # a control that passes everything, and for each expectation one it fails: a check never seen
    # failing might pass anything
    fails = [c["fails"] for c in e["controls"]]
    assert [] in fails
    assert set(ids) <= {c for f in fails for c in f}


@pytest.mark.parametrize(
    ("e", "control"),
    [(e, c) for e in EVALS for c in e["controls"]],
    ids=lambda x: x["key"] if "key" in x else ",".join(x["fails"]) or "passes",
)
def test_a_control_fails_exactly_the_checks_it_names(e, control):
    valid = {(p["name"], p["version"]) for p in e["policies"]}
    # as validation would leave it: what the grader sees
    assert clean_distillation(control["output"], valid) == control["output"]
    graded = grade_distill.grade(e, {"text": control["output"]})
    assert {c for c, (ok, _) in graded.items() if not ok} == set(control["fails"])


@pytest.mark.parametrize("e", EVALS, ids=lambda e: e["key"])
def test_every_not_pattern_fires_on_some_control(e):
    # an expectation failed by some control can still hold a `not` pattern nothing has ever matched,
    # which might match nothing at all: each must be seen to fire on a control's bullets (as the grader
    # reads them). `says` patterns are left to the expectation-level check above: most only ask
    # that the bullet name its field.
    valid = {(p["name"], p["version"]) for p in e["policies"]}
    refs_of = {p["name"]: f"{p['name']}@{p['version']}" for p in e["policies"]}
    for x in e["expect"]:
        refs = [refs_of[n] for n in x["policies"]]
        texts = []
        for c in e["controls"]:
            cited = grade_distill.citing(
                grade_distill.bullets(c["output"], valid), refs
            )
            texts.append("\n".join(grade_distill.said(b[0]) for b in cited))
        for pat in x.get("not", []):
            assert any(
                re.search(pat, t, re.IGNORECASE | re.MULTILINE) for t in texts
            ), (
                x["id"],
                pat,
            )


E = {
    "key": "99-x",
    "resource_type": "aws.s3",
    "policies": [
        {"name": "a:one", "version": 1},
        {"name": "a:ten", "version": 10},
    ],
    "expect": [{"id": "x", "policies": ["a:one"], "says": ["bucket"]}],
}


@pytest.mark.parametrize(
    ("text", "failing"),
    [
        ("- Buckets are flagged. a:one@1 a:ten@10", set()),
        # a:ten@1 is no ref to a:ten@10, nor a:one@10 to a:one@1
        ("- Buckets are flagged. a:one@10", {"cited", "x"}),
        ("- What passes: buckets. a:one@1 a:ten@10", {"no-labels"}),
        ("- Volumes are flagged. a:one@1 a:ten@10", {"x"}),
    ],
)
def test_the_general_checks(text, failing):
    graded = grade_distill.grade(E, {"text": text})
    assert {c for c, (ok, _) in graded.items() if not ok} == failing


def test_a_policy_s_name_says_nothing():
    # "remove" only in "...-remove@1" is no saying that the rule is removed
    e = {
        "policies": [{"name": "a:sg-remove", "version": 1}],
        "expect": [{"id": "x", "policies": ["a:sg-remove"], "says": ["remov"]}],
    }
    graded = grade_distill.grade(
        e, {"text": "- Groups are flagged; reports only. a:sg-remove@1"}
    )
    assert graded["x"] == (False, "doesn't say /remov/")


def test_a_run_without_a_distillation_fails_every_check():
    graded = grade_distill.grade(E, {"rejected": "no bullets"})
    assert all(not ok for ok, _ in graded.values())
    assert set(graded) == {*grade_distill.GENERAL, "x"}


def test_the_runner_and_grader_end_to_end_with_a_stub(tmp_path, monkeypatch, capsys):
    e = EVALS[0]
    good = next(c["output"] for c in e["controls"] if not c["fails"])
    calls = []

    def stub(prompt, cmd, valid, raw):
        calls.append(prompt)
        raw.append("Model: Stub\n" + good)
        if len(calls) == 2:
            raise ValueError("model output contained no bullets")
        return "Stub", good, {"seconds": 2.0, "attempts": 1, "cost_usd": 0.01}

    monkeypatch.setattr(run_distill.distill, "distil_one", stub)
    run_distill.settle(
        tmp_path,
        {"model": "haiku", "command": ["claude"], "template": None},
        {e["key"]: run_distill.prompt_of(e)},
    )
    run_distill.run_one(tmp_path, e, 1, cmd=None)
    run_distill.run_one(tmp_path, e, 2, cmd=None)
    # done already: not run again
    assert "done already" in run_distill.run_one(tmp_path, e, 1, cmd=None)
    assert len(calls) == 2
    assert calls[0] == run_distill.prompt_of(e)
    assert (tmp_path / e["key"] / "run-1" / "prompt.txt").read_text() == calls[0]
    grade_distill.main(tmp_path)
    out = capsys.readouterr().out
    report = json.loads((tmp_path / e["key"] / "grading.json").read_text())
    assert report["made"] == {
        "passed": 1,
        "runs": 2,
        "failures": [{"run": "run-2", "why": "model output contained no bullets"}],
    }
    assert "1/2 made" in out
    first = json.loads((tmp_path / e["key"] / "run-1" / "result.json").read_text())
    assert first["raw"] == ["Model: Stub\n" + good]
    assert "2s a run, $0.01 in all" in out


def test_a_batch_never_mixes_settings_or_prompts(tmp_path):
    settings = {"model": "haiku", "command": ["claude"]}
    run_distill.settle(tmp_path, settings, {"a": "one", "b": "two"})
    # resumed as it was, or with an eval it hasn't run yet
    run_distill.settle(tmp_path, settings, {"a": "one"})
    run_distill.settle(tmp_path, settings, {"c": "three"})
    batch = json.loads((tmp_path / "batch.json").read_text())
    assert set(batch["prompts"]) == {"a", "b", "c"}
    # another model, or another prompt for an eval it has run (a bundle change, say), is refused
    with pytest.raises(SystemExit):
        run_distill.settle(tmp_path, {**settings, "model": "sonnet"}, {"a": "one"})
    with pytest.raises(SystemExit, match="b"):
        run_distill.settle(tmp_path, settings, {"b": "two, rebundled"})
    assert json.loads((tmp_path / "batch.json").read_text()) == batch


@pytest.mark.parametrize(
    ("template", "why"),
    [
        ("Distil these {rtype} policies.", "no {bundle}"),
        ("{rtype} {bundle} {other}", "not a template"),
    ],
)
def test_a_prompt_template_must_take_the_bundle(tmp_path, monkeypatch, template, why):
    # refused before any run: without {bundle} every run would distil no policy at all
    path = tmp_path / "prompt.txt"
    path.write_text(template, encoding="utf-8")
    monkeypatch.setattr(
        sys, "argv", ["run_distill.py", str(tmp_path / "out"), "--prompt", str(path)]
    )
    with pytest.raises(SystemExit, match=re.escape(why)):
        run_distill.main()
    assert not (tmp_path / "out").exists()


def batch_of(*evals, template=None):
    """A batch.json, as settle records it, of runs made from EVALS (with TEMPLATE)."""
    return {
        "template": template,
        "prompts": {
            e["key"]: run_distill.prompt_hash(run_distill.prompt_of(e, template))
            for e in evals
        },
    }


def test_the_grader_grades_only_runs_of_the_prompt_the_repo_builds_now():
    e = EVALS[0]
    # the repo's checks, whatever checks the batch had when it ran
    assert grade_distill.gradeable(batch_of(e)) == ([e], {})
    # a batch made with another template (a prompt being measured) is graded against it
    template = "Other words. {rtype}\n{bundle}"
    assert grade_distill.gradeable(batch_of(e, template=template)) == ([e], {})
    # runs made from other policies, or of an eval since removed: said, never graded
    moved = {**e, "policies": [{**e["policies"][0], "version": 99}]}
    gone = {**e, "key": "99-gone"}
    graded, skipped = grade_distill.gradeable(batch_of(moved, gone))
    assert graded == []
    assert set(skipped) == {e["key"], "99-gone"}
    assert "another prompt" in skipped[e["key"]]
    assert "no longer" in skipped["99-gone"]


def test_a_batch_the_repo_has_moved_on_from_is_reported_not_graded(tmp_path, capsys):
    e = EVALS[0]
    moved = {**e, "policies": [{**e["policies"][0], "version": 99}]}
    (tmp_path / "batch.json").write_text(json.dumps(batch_of(moved)))
    (tmp_path / e["key"] / "run-1").mkdir(parents=True)
    (tmp_path / e["key"] / "run-1" / "result.json").write_text('{"text": ""}')
    grade_distill.main(tmp_path)
    out = capsys.readouterr().out
    assert f"not graded: {e['key']}: its runs read another prompt" in out
    assert not (tmp_path / e["key"] / "grading.json").exists()
