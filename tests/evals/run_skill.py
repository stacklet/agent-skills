"""Run the skill-vs-no-skill evals (tests/evals/skill/) and lay the results out for grade_skill.py.

usage: run_skill.py OUT [--arms with_skill,without_skill] [--runs N] [--evals ID,..]
                        [--parallel N] [--model MODEL] [--no-warm] [--yes]

Each run is a headless `claude -p` agent session on your Claude account, so nothing starts without
--yes (the plan is printed); at the end the runner adds up the cost Claude Code reports. Then
`python3 tests/evals/grade_skill.py OUT/iter`. What it does, so runs are comparable wherever made:
- builds each fixture's cache once (FIXTURES: make_fixture.py in OUT/cache, make_estate.py in
  OUT/cache-estate), each as its own deployment, and on every start assesses its posture and
  pre-warms the distillations the evals need, so every run reads the same ones and pays for none. A
  type that fails to warm aborts the batch; rerunning fills it in (--no-warm skips warming).
- runs each agent in an empty repo dir outside OUT, under a neutral name in a private temp dir of
  its own, so `ls ..` finds no shared cache or other run (searching the whole filesystem still
  could: every run is the same user). Its eval's `start` is put in place first (set_up); with the
  skill it gets its own copy of the cache, without it no cache and no guardrails settings. Its
  outputs are copied to OUT/iter/eval-N-name/ARM/run-K/outputs.
- isolates the agent from this machine: project settings only (not the runner's own CLAUDE.md,
  plugins or skills), and no MCP servers (a configured Stacklet server would be chosen over the
  fixture cache) but, for a flow eval, the fake Stacklet server (mcp_config). with_skill loads the
  plugin from this checkout with --plugin-dir; without_skill doesn't load it.
- keeps each run's transcript.jsonl and result.json, a flow eval's mcp_log.jsonl and
  cache_state.json, and in OUT/run_info.json the model, Claude Code version and commit of each
  invocation.

A run with a result.json is skipped, so an interrupted batch can be rerun to finish; one that hit
the usage limit leaves none.
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grade_skill


HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1] / "plugins" / "stacklet"
SCRIPTS = PLUGIN / "skills" / "guardrails" / "scripts"
# the fixtures are shared with the unit tests
FIXTURE_DIR = HERE.parent / "fixture"
# the deployment make_fixture.py's responses come from (its CONSOLE)
FIXTURE_DEPLOYMENT = "console.fixture.example"
ARMS = ("with_skill", "without_skill")


sys.path.insert(0, str(FIXTURE_DIR))
from make_fixture import cid, stamp


# Each fixture an eval can run against (its eval.json `fixture`, default "fixture"): the script that
# makes it, the deployment it is ingested as, the cache directory under OUT, the resource types to
# warm ("bound": every type a bound collection has policies for), and the collections to include
# when warming beyond what applies by default (the ones evals bring in by an answer or an account).
FIXTURES = {
    "fixture": {
        "make": "make_fixture.py",
        "deployment": FIXTURE_DEPLOYMENT,
        "cache": "cache",
        "types": (
            "aws.s3",
            "aws.rds",
            "aws.ec2",
            "aws.sqs",
            "aws.lambda",
            "aws.ebs",
            "aws.security-group",
        ),
        "include": (cid("PCI-DSS AWS"), cid("Sandbox Tagging")),  # evals 2, 11 and 31
    },
    "estate": {
        "make": "make_estate.py",
        "deployment": "console.estate.example",
        "cache": "cache-estate",
        # every type a bound collection has policies for: agents look up more than the evals'
        # SHOWS (security groups, databases, functions), and each must read a warmed one
        "types": "bound",
        # every bound collection: what is warmed must not depend on the assessment's verdicts
        "include": "all",
    },
}


def script(name, *args, cache, cwd, deployment):
    env = dict(os.environ, STACKLET_GUARDRAILS_CACHE=str(cache))
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / name), deployment, *map(str, args)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if r.returncode:
        sys.exit(f"{name} {' '.join(map(str, args))} failed:\n{r.stderr}")
    return r


def bound_types(cache, dep):
    """Every resource type the policies of DEP's bound collections in CACHE are for."""
    db = sqlite3.connect(cache / dep / "posture.db")
    try:
        db.execute("ATTACH DATABASE ? AS g", (str(cache / dep / "guardrails.db"),))
        return [
            t
            for (t,) in db.execute(
                "SELECT DISTINCT p.resource_type FROM g.collection_policy cp JOIN g.policy p"
                " ON p.uuid = cp.policy_uuid AND p.version = cp.policy_version"
                " WHERE EXISTS (SELECT 1 FROM binding b"
                " WHERE b.collection_uuid = cp.collection_uuid) ORDER BY 1"
            )
        ]
    finally:
        db.close()


def prepare(out, warm, fixture="fixture"):
    """FIXTURE's cache (see FIXTURES) under OUT, built once, and with WARM its posture assessed and
    every type distilled in it before any run starts. Its own directory, holding only its own
    deployment, so a run's deployments.py picks it without asking. Warming happens on every start,
    not only when the cache is built: a type that failed to warm aborts the batch (every run must
    read the same distillations, or runs aren't comparable), and a rerun into the same OUT must fill
    that type in rather than leave each run to distil its own. What is cached already costs
    nothing."""
    f = FIXTURES[fixture]
    dep = f["deployment"]
    cache = out / f["cache"]

    def run(name, *args, cwd=out):
        return script(name, *args, cache=cache, cwd=cwd, deployment=dep)

    if not cache.exists():
        # built aside and moved into place whole, so a failed build is redone on the next start
        # rather than left half made
        building = out / f"{f['cache']}.building"
        shutil.rmtree(building, ignore_errors=True)
        fx = out / fixture
        subprocess.run(
            [sys.executable, str(FIXTURE_DIR / f["make"]), str(fx)], check=True
        )
        script(
            "ingest.py",
            "posture",
            fx / "posture.json",
            cache=building,
            cwd=out,
            deployment=dep,
        )
        # as fetched under that posture, as scope.py query's queries mark it
        stamp(fx / "collections.json", building / dep / "posture.db")
        for args in (
            ("collections", fx / "collections.json"),
            ("accounts", *sorted(fx.glob("account_*.json"))),
        ):
            script("ingest.py", *args, cache=building, cwd=out, deployment=dep)
        os.replace(building, cache)
    if warm:
        # distill.py only shows what is in scope, so warm from a scratch repo that includes what
        # evals bring in beyond the defaults
        scratch = out / ("warm" if fixture == "fixture" else f"warm-{fixture}")
        scratch.mkdir(exist_ok=True)
        if not (scratch / ".stacklet" / "guardrails" / "scope.json").exists():
            include = f["include"]
            if include == "all":
                db = sqlite3.connect(cache / dep / "posture.db")
                include = [
                    u
                    for (u,) in db.execute(
                        "SELECT uuid FROM policy_collection c WHERE c.policy_count > 0 AND EXISTS"
                        " (SELECT 1 FROM binding b WHERE b.collection_uuid = c.uuid)"
                    )
                ]
                db.close()
            run(
                "scope.py",
                "decide",
                "--include",
                *include,
                "--because",
                "pre-warm",
                cwd=scratch,
            )
        # the posture assessment decides each run's scope, so every run must read the same one too;
        # first, so what is warmed is what will be in scope
        print(f"{fixture}: assessing the posture (a model call, unless cached)...")
        run("assess.py", "show", cwd=scratch)
        types = f["types"]
        if types == "bound":
            types = bound_types(cache, dep)
        print(
            f"{fixture}: pre-warming {len(types)} resource types (a model call each one "
            "not cached)..."
        )
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(lambda t: run("distill.py", "show", t, cwd=scratch), types))
    return cache


def git(repo, *args):
    """Run git in REPO as a throwaway identity (the runner's own config may not have one)."""
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=dev",
            "-c",
            "user.email=dev@example.com",
            *args,
        ],
        check=True,
    )


def set_up(e, outputs, cache):
    """What eval E needs in place before its agent starts (its eval.json `start`)."""
    start = e.get("start", {})
    f = FIXTURES[e.get("fixture", "fixture")]
    dep = f["deployment"]
    # a scope decision already recorded (a collection of make_fixture's)
    if "exclude" in start:
        script(
            "scope.py",
            "decide",
            "--exclude",
            cid(start["exclude"]["collection"]),
            "--because",
            start["exclude"]["because"],
            cache=cache,
            cwd=outputs,
            deployment=dep,
        )
    if (
        start.get("cache") == "none"
        or "unload" in start
        or start.get("forget_accounts")
    ):
        # what a sync would have fetched, gone: for a flow eval, whose agent fetches it through the
        # fake server. The assessment and the distillations stay (keyed by the bindings and by
        # each collection's policies, which the fake server serves unchanged), so the run pays for
        # no model call but the agent's.
        con = sqlite3.connect(cache / dep / "guardrails.db")
        with con:
            if start.get("cache") == "none":
                for table in ("collection_policy", "membership", "policy", "account"):
                    con.execute(f"DELETE FROM {table}")
            for name in start.get("unload", []):
                for table in ("collection_policy", "membership"):
                    con.execute(
                        f"DELETE FROM {table} WHERE collection_uuid = ?", (cid(name),)
                    )
            if start.get("forget_accounts"):
                con.execute("DELETE FROM account")
        con.close()
        if start.get("cache") == "none":
            (cache / dep / "posture.db").unlink()
    if "posture_refreshed_at" in start:  # an aged posture
        con = sqlite3.connect(cache / dep / "posture.db")
        con.execute(
            "UPDATE meta SET value = ? WHERE key = 'refreshed_at'",
            (start["posture_refreshed_at"],),
        )
        con.commit()
        con.close()
    if "copy" in start:  # an existing repo
        shutil.copytree(FIXTURE_DIR / start["copy"], outputs, dirs_exist_ok=True)
    if "commit" in start:
        # committed, so the agent (and grade_skill.py) can see what the change touched
        git(outputs, "init", "-q", "-b", "main")
        git(outputs, "add", "-A")
        git(outputs, "commit", "-qm", start["commit"])
    if "branch" in start:  # work on a feature branch: in the code, but never deployed
        branch = start["branch"]
        git(outputs, "checkout", "-qb", branch["name"])
        shutil.copytree(FIXTURE_DIR / branch["copy"], outputs, dirs_exist_ok=True)
        git(outputs, "add", "-A")
        git(outputs, "commit", "-qm", branch["commit"])


def mcp_config(e, fixture_dir, log):
    """The MCP servers eval E's agent gets: none, or for a flow eval the fake Stacklet server over
    FIXTURE_DIR, logging to LOG."""
    if e.get("kind") != "flow":
        return {"mcpServers": {}}
    opts = e.get("mcp") or {}
    args = [str(FIXTURE_DIR / "fake_stacklet_mcp.py"), str(fixture_dir), str(log)]
    for o in ("pad", "delay"):
        if o in opts:
            args += [f"--{o}", str(opts[o])]
    if opts.get("fail"):  # last: it takes the rest
        args += ["--fail", *(cid(n) for n in opts["fail"])]
    return {"mcpServers": {"stacklet": {"command": sys.executable, "args": args}}}


def agent_command(prompt, arm, model, mcp=None):
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        # only the MCP servers given, and none of the runner's own settings
        "--strict-mcp-config",
        "--mcp-config",
        json.dumps(mcp or {"mcpServers": {}}),
        "--setting-sources",
        "project",
        "--dangerously-skip-permissions",
        # a scheduled wakeup keeps a headless session open until it fires: one agent set a 20-minute
        # fallback while a background sync ran, and its finished run idled until the timeout
        "--disallowedTools",
        "ScheduleWakeup",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if arm == "with_skill":
        cmd += ["--plugin-dir", str(PLUGIN)]
    return cmd


def run_one(out, caches, e, arm, k, model):
    rundir = out / "iter" / f"eval-{e['id']}-{e['name']}" / arm / f"run-{k}"
    if (rundir / "result.json").exists():
        return f"{e['name']} {arm} run-{k}: done already"
    if rundir.exists():  # an interrupted run: start it again from scratch
        shutil.rmtree(rundir)
    rundir.mkdir(parents=True)
    cache = caches[e.get("fixture", "fixture")]
    env = agent_env(os.environ)
    # outside OUT, where `ls ..` would reach the shared caches and other runs (module docstring)
    with tempfile.TemporaryDirectory() as private:
        tmp = Path(tempfile.mkdtemp(prefix="work_", dir=private))
        outputs, run_cache = tmp / "outputs", tmp / "cache"
        outputs.mkdir()
        # in both arms: setup may write a scope file into the repo, which scope.py checks against
        # the cache
        shutil.copytree(cache, run_cache)
        set_up(e, outputs, run_cache)
        if arm == "with_skill":
            env["STACKLET_GUARDRAILS_CACHE"] = str(run_cache)
        else:
            # no cache, and nothing in its environment that names the skill's settings (the cache
            # this runner was given, an inference command) either
            shutil.rmtree(run_cache)
            env = {
                key: v
                for key, v in env.items()
                if not key.startswith("STACKLET_GUARDRAILS_")
            }
        mcp = mcp_config(e, out / e.get("fixture", "fixture"), rundir / "mcp_log.jsonl")
        with open(rundir / "transcript.jsonl", "w", encoding="utf-8") as transcript:
            r = subprocess.run(
                agent_command(e["prompt"], arm, model, mcp),
                cwd=outputs,
                env=env,
                stdout=transcript,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                timeout=1800,
                check=False,
            )
        result = run_result(rundir / "transcript.jsonl")
        if usage_limited(result):
            # not a run of the eval at all: gone, so it isn't graded and rerunning redoes it
            shutil.rmtree(rundir)
            return f"{e['name']} {arm} run-{k}: usage limit reached ({result.get('result')}); rerun later"
        if e.get("kind") == "flow":
            cache_state(run_cache, e, rundir / "cache_state.json")
        # not a `terraform init`'s providers: hundreds of MB a run, which no check reads
        shutil.copytree(
            outputs,
            rundir / "outputs",
            symlinks=True,
            ignore=shutil.ignore_patterns(".terraform"),
        )
    outputs = rundir / "outputs"
    result["returncode"], result["stderr"] = r.returncode, r.stderr[-2000:]
    (outputs / "final_response.md").write_text(
        result.get("result") or "", encoding="utf-8"
    )
    (rundir / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    cost = result.get("total_cost_usd") or 0
    return f"{e['name']} {arm} run-{k}: rc={r.returncode} ${cost:.2f} turns={result.get('num_turns')}"


# the runner's machine's cloud identity: an agent reading it would look up the runner's own
# accounts (flow evals have a server to ask), and its results would depend on the machine
CLOUD_ENV = ("AWS_", "GOOGLE_", "CLOUDSDK_", "AZURE_", "ARM_", "GCLOUD_")


def agent_env(environ):
    """The environment an eval's agent runs in: the runner's, without its cloud identity, and
    without CLAUDE_CODE_EXECPATH (the agent's nested distillation calls must find its own claude,
    not this process's)."""
    return {
        k: v
        for k, v in environ.items()
        if k != "CLAUDE_CODE_EXECPATH" and not k.startswith(CLOUD_ENV)
    }


def usage_limited(result):
    """Whether a run's RESULT message says the account's usage limit stopped it: not a run of the
    eval at all. `api_error` is in Claude Code builds of late 2026 (an internal field, absent from
    older ones), so the message's text counts too."""
    return result.get("api_error") == "usage_limit_reached" or bool(
        result.get("is_error")
        and re.search(
            r"usage limit|session limit", str(result.get("result", "")), re.IGNORECASE
        )
    )


def run_result(transcript):
    """The result message the agent's session ended with ({} if none)."""
    result = {}
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "result":
            result = msg
    return result


def cache_state(cache, e, path):
    """What flow eval E's run left in its CACHE, written to PATH for its checks
    (grade_skill.cache_state): what was ingested, not just what was fetched. {"refresh_id": the
    posture's or None, "memberships": {collection uuid: the refresh id it was fetched under},
    "accounts": [PROVIDER:KEY looked up]}."""
    d = cache / FIXTURES[e.get("fixture", "fixture")]["deployment"]
    state = {"refresh_id": None, "memberships": {}, "accounts": []}
    if (d / "posture.db").exists():
        con = sqlite3.connect(d / "posture.db")
        state["refresh_id"] = con.execute(
            "SELECT value FROM meta WHERE key = 'refresh_id'"
        ).fetchone()[0]
        con.close()
    if (d / "guardrails.db").exists():
        con = sqlite3.connect(d / "guardrails.db")
        state["memberships"] = dict(con.execute("SELECT * FROM membership"))
        state["accounts"] = sorted(
            f"{p}:{k}" for p, k in con.execute("SELECT provider, key FROM account")
        )
        con.close()
    path.write_text(json.dumps(state, indent=1), encoding="utf-8")


def describe_checkout():
    def git(*args):
        r = subprocess.run(
            ["git", *args], cwd=HERE, capture_output=True, text=True, check=False
        )
        return r.stdout.strip()

    return {
        "commit": git("rev-parse", "HEAD"),
        "dirty": bool(git("status", "--porcelain")),
    }


def main():
    ap = argparse.ArgumentParser(description="Run the evals; see the module docstring.")
    ap.add_argument("out", type=Path, help="output directory (reused to resume)")
    ap.add_argument(
        "--arms", default="with_skill", help=f"comma-separated: {', '.join(ARMS)}"
    )
    ap.add_argument(
        "--runs", type=int, default=3, help="runs per eval and arm (default 3)"
    )
    ap.add_argument("--evals", help="comma-separated eval ids (default: all)")
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument("--model", default="opus", help="the agent's model (default opus)")
    ap.add_argument(
        "--no-warm",
        action="store_true",
        help="don't assess the posture or pre-warm distillations before the runs",
    )
    ap.add_argument(
        "--yes", action="store_true", help="start (without it, print the plan)"
    )
    a = ap.parse_args()
    arms = a.arms.split(",")
    if set(arms) - set(ARMS):
        sys.exit(f"unknown arm(s): {', '.join(set(arms) - set(ARMS))}")
    evals = grade_skill.evals()
    if a.evals:
        wanted = {int(i) for i in a.evals.split(",")}
        unknown = wanted - {e["id"] for e in evals}
        if unknown:
            sys.exit(f"no such eval: {', '.join(map(str, sorted(unknown)))}")
        evals = [e for e in evals if e["id"] in wanted]
    # a flow eval measures what the skill makes the agent do: nothing to compare without it
    jobs = [
        (e, arm, k)
        for e in evals
        for arm in arms
        if not (e.get("kind") == "flow" and arm == "without_skill")
        for k in range(1, a.runs + 1)
    ]
    flows = sum(e.get("kind") == "flow" for e in evals)
    print(
        f"{len(jobs)} agent runs ({len(evals)} evals x {', '.join(arms)} x {a.runs}"
        + (
            f"; the {flows} flow evals with the skill only"
            if flows and len(arms) > 1
            else ""
        )
        + f") on {a.model}, {a.parallel} at a time"
    )
    if not a.yes:
        print("add --yes to start")
        return
    a.out.mkdir(parents=True, exist_ok=True)
    version = subprocess.run(
        ["claude", "--version"], capture_output=True, text=True, check=False
    ).stdout.strip()
    # one entry per invocation: a rerun into OUT (to finish a batch, or redo some evals) may be of
    # another commit, and what each measured must stay known
    info = a.out / "run_info.json"
    started = json.loads(info.read_text(encoding="utf-8")) if info.exists() else []
    started.append(
        {
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": a.model,
            "claude_code": version,
            "arms": arms,
            "runs": a.runs,
            "evals": [e["id"] for e in evals],
            **describe_checkout(),
        }
    )
    info.write_text(json.dumps(started, indent=1), encoding="utf-8")
    # only the fixtures these evals use: each costs an assessment and distillations to warm
    caches = {
        f: prepare(a.out, warm=not a.no_warm, fixture=f)
        for f in sorted({e.get("fixture", "fixture") for e in evals})
    }
    with ThreadPoolExecutor(a.parallel) as ex:
        for msg in ex.map(lambda j: run_one(a.out, caches, *j, a.model), jobs):
            print(msg, flush=True)
    # every run in OUT, including any finished by an earlier invocation
    costs = [
        json.loads(p.read_text(encoding="utf-8")).get("total_cost_usd") or 0
        for p in (a.out / "iter").glob("*/*/run-*/result.json")
    ]
    print(f"cost reported by Claude Code for {len(costs)} run(s): ${sum(costs):.2f}")
    print(f"done: python3 {HERE / 'grade_skill.py'} {a.out / 'iter'}")


if __name__ == "__main__":
    main()
