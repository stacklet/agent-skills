"""The eval runner's preparation: no agent runs, and distillation through the stub `claude`."""

import json
import sqlite3
import subprocess
import sys

from pathlib import Path

import grade_skill
import pytest
import run_skill


@pytest.fixture
def stub(fake_claude, monkeypatch):
    # script() passes the runner's own environment on: point it at the stub, never a real model
    monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
    for k, v in fake_claude.env.items():
        monkeypatch.setenv(k, v)
    return fake_claude


def test_a_type_that_fails_to_warm_aborts_and_a_rerun_fills_it_in(
    tmp_path, stub, monkeypatch
):
    out = tmp_path / "out"
    monkeypatch.setenv("FAKE_CLAUDE_JUNK", "1")  # every distillation is rejected
    with pytest.raises(SystemExit, match="distill.py show"):
        run_skill.prepare(out, warm=True)

    # the cache was built, but warming happens on every start: a rerun fills in what failed rather
    # than leave each run to distil its own
    monkeypatch.delenv("FAKE_CLAUDE_JUNK")
    before = stub.calls()
    run_skill.prepare(out, warm=True)
    assert stub.calls() > before

    # the assessment is made too, by the same command
    db = sqlite3.connect(out / "cache" / run_skill.FIXTURE_DEPLOYMENT / "guardrails.db")
    assert db.execute("SELECT model FROM assessment").fetchall() == [
        ("fake-model 1.0",)
    ]

    # and once everything is warm, a rerun makes no model calls
    done = stub.calls()
    run_skill.prepare(out, warm=True)
    assert stub.calls() == done


def test_the_estate_fixture_gets_a_cache_of_its_own(tmp_path, stub):
    out = tmp_path / "out"
    cache = run_skill.prepare(out, warm=True, fixture="estate")
    assert cache == out / "cache-estate"
    # its deployment only, so a run's deployments.py picks it without asking
    assert [p.name for p in cache.iterdir()] == ["console.estate.example"]
    db = sqlite3.connect(cache / "console.estate.example" / "guardrails.db")
    assert db.execute("SELECT model FROM assessment").fetchall() == [
        ("fake-model 1.0",)
    ]
    assert db.execute("SELECT count(*) FROM account").fetchone() == (5,)
    # every type a bound collection has policies for, whatever the evals' SHOWS name: agents look
    # up others too, and each must read a warmed distillation
    warmed = {
        t for (t,) in db.execute("SELECT DISTINCT resource_type FROM distillation")
    }
    assert {"aws.security-group", "aws.rds", "aws.lambda", "gcp.bucket"} <= warmed
    assert warmed == set(run_skill.bound_types(cache, "console.estate.example"))


def test_every_eval_names_a_known_fixture_and_an_existing_repo():
    for e in grade_skill.evals():
        assert e.get("fixture", "fixture") in run_skill.FIXTURES, e["name"]
        start = e.get("start", {})
        for repo in (start.get("copy"), start.get("branch", {}).get("copy")):
            if repo:
                assert (run_skill.FIXTURE_DIR / repo).is_dir(), (e["name"], repo)


def test_runs_work_outside_out_and_only_the_skill_s_arm_gets_a_cache(
    tmp_path, monkeypatch
):
    # inside OUT a few `ls ..` reach the shared caches and other runs, so a run without the skill
    # could read the skill's data
    seen = {}

    def fake_run(cmd, cwd, env, **kw):
        cache = env.get("STACKLET_GUARDRAILS_CACHE")
        seen.update(
            cwd=Path(cwd),
            cmd=cmd,
            env=dict(env),
            cache=Path(cache) if cache else None,
            held=sorted(p.name for p in Path(cache).iterdir()) if cache else None,
            # what `ls ..` and `ls ../..` show the agent
            beside=sorted(p.name for p in Path(cwd).parent.iterdir()),
            above=sorted(p.name for p in Path(cwd).parent.parent.iterdir()),
            mode=Path(cwd).parent.parent.stat().st_mode & 0o777,
        )
        (Path(cwd) / "main.tf").write_text("# the agent's work")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(run_skill.subprocess, "run", fake_run)
    monkeypatch.setenv("STACKLET_GUARDRAILS_CACHE", str(tmp_path / "mine"))
    monkeypatch.setenv("STACKLET_GUARDRAILS_INFERENCE_CMD", '["mine"]')
    cache = tmp_path / "cache"
    (cache / "console.fixture.example").mkdir(parents=True)
    out = tmp_path / "out"
    e = {"id": 99, "name": "x", "prompt": "p"}
    for arm in ("without_skill", "with_skill"):
        run_skill.run_one(out, {"fixture": cache}, e, arm, 1, "opus")
        rundir = out / "iter" / "eval-99-x" / arm / "run-1"
        assert out not in seen["cwd"].parents
        # a neutral name, alone in a private dir: no other run's beside it
        assert seen["cwd"].parent.name.startswith("work_")
        assert seen["above"] == [seen["cwd"].parent.name]
        assert seen["mode"] == 0o700
        # its work is graded from the run's dir
        assert (rundir / "outputs" / "main.tf").read_text() == "# the agent's work"
        if arm == "without_skill":
            # no cache, nor any guardrails setting of the runner's
            assert seen["cache"] is None
            assert not [k for k in seen["env"] if k.startswith("STACKLET_GUARDRAILS_")]
            assert seen["beside"] == ["outputs"]
            assert "--plugin-dir" not in seen["cmd"]
        else:
            assert out not in seen["cache"].parents
            # never the real ~/.stacklet either
            assert seen["cache"] != tmp_path / "mine"
            assert seen["held"] == ["console.fixture.example"]
            assert "--plugin-dir" in seen["cmd"]


def test_a_failed_build_is_redone_on_the_next_start(tmp_path, stub, monkeypatch):
    out = tmp_path / "out"
    real = run_skill.script

    def failing(name, *args, **kw):
        if args and args[0] == "accounts":
            raise SystemExit("ingest failed")
        return real(name, *args, **kw)

    monkeypatch.setattr(run_skill, "script", failing)
    with pytest.raises(SystemExit):
        run_skill.prepare(out, warm=False)
    assert not (out / "cache").exists()  # not left half made, to be taken as built
    monkeypatch.setattr(run_skill, "script", real)
    cache = run_skill.prepare(out, warm=False)
    db = sqlite3.connect(cache / run_skill.FIXTURE_DEPLOYMENT / "guardrails.db")
    # account_0, account_1
    assert db.execute("SELECT count(*) FROM account").fetchone() == (2,)


def fake_agent(seen, result=None):
    """A stand-in for subprocess.run of the agent: notes what it was given (and the cache it found)
    in SEEN, and ends its transcript with RESULT (a result message)."""

    def run(cmd, cwd, env, stdout, **kw):
        d = Path(env["STACKLET_GUARDRAILS_CACHE"]) / run_skill.FIXTURE_DEPLOYMENT
        db = sqlite3.connect(d / "guardrails.db")
        seen.update(
            cmd=cmd,
            posture=(d / "posture.db").exists(),
            memberships=dict(db.execute("SELECT * FROM membership")),
            accounts=db.execute("SELECT count(*) FROM account").fetchone()[0],
        )
        db.close()
        stdout.write(json.dumps(result or {"type": "result", "result": "done"}) + "\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    return run


def test_a_flow_eval_starts_without_what_a_sync_fetches_and_gets_the_server(
    tmp_path, stub, monkeypatch
):
    out = tmp_path / "out"
    caches = {"fixture": run_skill.prepare(out, warm=False)}
    seen = {}
    monkeypatch.setattr(run_skill.subprocess, "run", fake_agent(seen))
    e = {
        "id": 99,
        "name": "x",
        "prompt": "p",
        "kind": "flow",
        "start": {"cache": "none"},
        "mcp": {"pad": 5, "fail": ["Acme Org Standards"]},
    }
    run_skill.run_one(out, caches, e, "with_skill", 1, "opus")
    # nothing a sync would fetch: no posture, memberships or account lookups
    assert (seen["posture"], seen["memberships"], seen["accounts"]) == (False, {}, 0)
    config = json.loads(seen["cmd"][seen["cmd"].index("--mcp-config") + 1])
    args = config["mcpServers"]["stacklet"]["args"]
    assert args[0].endswith("fake_stacklet_mcp.py")
    assert args[3:] == ["--pad", "5", "--fail", run_skill.cid("Acme Org Standards")]
    # what it left, for the checks
    rundir = out / "iter" / "eval-99-x" / "with_skill" / "run-1"
    state = json.loads((rundir / "cache_state.json").read_text())
    assert state == {"refresh_id": None, "memberships": {}, "accounts": []}

    # or just some collections and the lookups
    e = {
        **e,
        "id": 98,
        "start": {"unload": ["Sandbox Tagging"], "forget_accounts": True},
    }
    run_skill.run_one(out, caches, e, "with_skill", 1, "opus")
    assert seen["posture"]
    assert run_skill.cid("Sandbox Tagging") not in seen["memberships"]
    assert run_skill.cid("CIS AWS Baseline") in seen["memberships"]
    assert seen["accounts"] == 0


def test_a_run_that_hit_the_usage_limit_is_not_graded_and_is_redone(
    tmp_path, stub, monkeypatch
):
    out = tmp_path / "out"
    caches = {"fixture": run_skill.prepare(out, warm=False)}
    limited = {
        "type": "result",
        "is_error": True,
        "api_error": "usage_limit_reached",
        "result": "You've hit your session limit",
    }
    monkeypatch.setattr(run_skill.subprocess, "run", fake_agent({}, limited))
    e = {"id": 97, "name": "x", "prompt": "p"}
    msg = run_skill.run_one(out, caches, e, "with_skill", 1, "opus")
    assert "usage limit reached" in msg
    # nothing left to grade
    assert not (out / "iter" / "eval-97-x" / "with_skill" / "run-1").exists()


@pytest.mark.parametrize(
    ("result", "limited"),
    [
        ({"api_error": "usage_limit_reached"}, True),
        # older Claude Code builds have no api_error value for it: the message says so
        (
            {"is_error": True, "result": "You've hit your session limit · resets 5pm"},
            True,
        ),
        ({"is_error": True, "result": "Claude AI usage limit reached"}, True),
        # a run that merely talks about limits, or failed otherwise, is a run
        ({"is_error": False, "result": "The queue has no usage limit."}, False),
        ({"is_error": True, "result": "API Error: 500"}, False),
    ],
)
def test_a_usage_limited_run_is_recognised_whatever_the_build(result, limited):
    assert run_skill.usage_limited(result) is limited


def test_the_agent_gets_none_of_the_runners_cloud_identity():
    env = run_skill.agent_env(
        {
            "PATH": "/bin",
            "HOME": "/home/x",
            "AWS_PROFILE": "runner",
            "AWS_ACCESS_KEY_ID": "AKIA",
            "GOOGLE_APPLICATION_CREDENTIALS": "/k.json",
            "CLOUDSDK_CORE_PROJECT": "p",
            "AZURE_SUBSCRIPTION_ID": "s",
            "ARM_CLIENT_ID": "c",
            "CLAUDE_CODE_EXECPATH": "/x/claude",
            "STACKLET_GUARDRAILS_CACHE": "/c",
        }
    )
    assert env == {"PATH": "/bin", "HOME": "/home/x", "STACKLET_GUARDRAILS_CACHE": "/c"}


def test_an_unknown_eval_id_is_refused(tmp_path):
    # rather than a batch that silently runs nothing
    r = subprocess.run(
        [
            sys.executable,
            str(Path(run_skill.__file__)),
            str(tmp_path),
            "--evals",
            "1,999",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode != 0
    assert "no such eval: 999" in r.stderr
