"""Run the posture assessment's evals (tests/evals/assess/) and lay the results out for
grade_assess.py. tests/evals/README.md says what they are and how to run them.

usage: run_assess.py OUT [--runs N] [--model MODEL] [--evals ID,..] [--parallel N] [--yes]

Every run is a fresh assessment: the posture ingested into a cache of its own
(OUT/EVAL/run-K/cache) and `assess.py show` run on it, so no run sees another's. Each eval also
gets one reassessment of its first run's cache (OUT/EVAL/reassess), offered that run's questions,
to see whether they are kept. A run is one model call (the assessment's, with --model, default
sonnet; two if its first reply is rejected, none when rules decide everything), so without --yes
only the plan is printed. A run with a result.json, failed or not, is skipped, so a batch can be
resumed: delete a failed run's directory to run it again (a remade run 1 remakes the
reassessment too). OUT/evals.json, which the grader reads, is written before any run and keeps
the evals of earlier batches into OUT. Then `python3 tests/evals/grade_assess.py OUT`.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVALS = HERE / "assess"
SCRIPTS = HERE.parents[1] / "plugins" / "stacklet" / "skills" / "guardrails" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from _cache import deployment_name


def evals():
    """Every assessment eval, in id order: its eval.json plus its id, name, dir and posture."""
    out = []
    for d in sorted(EVALS.glob("[0-9]*-*/")):
        n, name = d.name.split("-", 1)
        e = json.loads((d / "eval.json").read_text(encoding="utf-8"))
        out.append(
            {
                "id": int(n),
                "name": name,
                "key": d.name,
                "posture": d / "posture.json",
                **e,
            }
        )
    return out


def deployment(posture):
    """The deployment POSTURE (a posture.json, parsed) is of, named as the scripts name it."""
    return deployment_name(posture["data"]["platform"]["urls"]["console"])


def script(name, *args, cache, model, cwd):
    env = dict(os.environ, STACKLET_GUARDRAILS_CACHE=str(cache))
    env["STACKLET_GUARDRAILS_ASSESS_MODEL"] = model
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name), *map(str, args)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def assessed(cache, dep):
    """The assessment row assess.py cached, as a dict, or None."""
    path = cache / dep / "guardrails.db"
    if not path.exists():
        return None
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT * FROM assessment ORDER BY created DESC, rowid DESC LIMIT 1"
    ).fetchone()
    db.close()
    if row is None:
        return None
    return {
        "assessment": json.loads(row["assessment"]),
        "model": row["model"],
        "stats": json.loads(row["stats"] or "null"),
        "inference_cmd": json.loads(row["inference_cmd"]),
    }


def run_one(out, e, k, model):
    """Run K of eval E: a fresh cache, the posture ingested, and an assessment. K "reassess"
    reassesses run 1's cache instead."""
    rundir = out / e["key"] / ("reassess" if k == "reassess" else f"run-{k}")
    if (rundir / "result.json").exists():
        return f"{e['key']} {rundir.name}: done already"
    shutil.rmtree(rundir, ignore_errors=True)
    if k == 1:
        # the reassessment is of run 1's cache: one of a run 1 since remade would be stale
        shutil.rmtree(out / e["key"] / "reassess", ignore_errors=True)
    rundir.mkdir(parents=True)
    posture = json.loads(e["posture"].read_text(encoding="utf-8"))
    dep = deployment(posture)
    cache = rundir / "cache"
    if k == "reassess":
        first = out / e["key"] / "run-1" / "cache"
        if not (first / dep / "guardrails.db").exists():
            return f"{e['key']} reassess: no run-1 assessment to reassess"
        shutil.copytree(first, cache)
        args = ("show", "--reassess")
    else:
        (rundir / "posture.json").write_text(json.dumps(posture), encoding="utf-8")
        r = script(
            "ingest.py",
            dep,
            "posture",
            rundir / "posture.json",
            cache=cache,
            model=model,
            cwd=rundir,
        )
        if r.returncode:
            (rundir / "result.json").write_text(
                json.dumps({"rc": r.returncode, "err": r.stderr[-1000:]}),
                encoding="utf-8",
            )
            return f"{e['key']} {rundir.name}: ingest failed"
        args = ("show",)
    r = script("assess.py", dep, *args, cache=cache, model=model, cwd=rundir)
    result = {"rc": r.returncode, "err": r.stderr[-1000:]}
    if r.returncode == 0:
        # only then is the cached row this run's: a failed reassessment leaves run 1's copy
        result.update(assessed(cache, dep) or {})
    (rundir / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    took = (result.get("stats") or {}).get("seconds")
    return f"{e['key']} {rundir.name}: rc={r.returncode}" + (
        f" {took}s" if took else ""
    )


def record(out, todo):
    """Add the evals TODO to OUT/evals.json, keeping those of earlier batches into OUT: written
    before any run, so an interrupted batch is gradeable and a partial resume loses nothing."""
    path = out / "evals.json"
    known = {}
    if path.exists():
        known = {e["key"]: e for e in json.loads(path.read_text(encoding="utf-8"))}
    known.update({e["key"]: {**e, "posture": str(e["posture"])} for e in todo})
    path.write_text(
        json.dumps([known[k] for k in sorted(known)], indent=1), encoding="utf-8"
    )


def main():
    ap = argparse.ArgumentParser(
        description="Run the assessment evals; see the module docstring."
    )
    ap.add_argument("out", type=Path)
    ap.add_argument(
        "--runs", type=int, default=3, help="fresh assessments per eval (default 3)"
    )
    ap.add_argument(
        "--model", default="sonnet", help="the assessment's model (default sonnet)"
    )
    ap.add_argument(
        "--evals", help="comma-separated eval ids (default: all in the repo)"
    )
    ap.add_argument("--parallel", type=int, default=6)
    ap.add_argument(
        "--yes", action="store_true", help="start (without it, print the plan)"
    )
    a = ap.parse_args()
    todo = evals()
    if a.evals:
        wanted = {int(i) for i in a.evals.split(",")}
        unknown = wanted - {e["id"] for e in todo}
        if unknown:
            sys.exit(f"no such eval: {', '.join(map(str, sorted(unknown)))}")
        todo = [e for e in todo if e["id"] in wanted]
    jobs = [(e, k) for e in todo for k in range(1, a.runs + 1)]
    print(
        f"{len(jobs)} assessments ({len(todo)} postures x {a.runs}) "
        f"plus {len(todo)} reassessments, on {a.model}, {a.parallel} at a time"
    )
    if not a.yes:
        print("add --yes to start")
        return
    a.out.mkdir(parents=True, exist_ok=True)
    record(a.out, todo)
    with ThreadPoolExecutor(a.parallel) as ex:
        for msg in ex.map(lambda j: run_one(a.out, *j, a.model), jobs):
            print(msg, flush=True)
        for msg in ex.map(lambda e: run_one(a.out, e, "reassess", a.model), todo):
            print(msg, flush=True)
    print(f"done: python3 {HERE / 'grade_assess.py'} {a.out}")


if __name__ == "__main__":
    main()
