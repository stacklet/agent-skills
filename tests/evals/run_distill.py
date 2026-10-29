"""Run the distillation's evals (tests/evals/distill/): fresh distillations, made as `distill.py show`
makes them, laid out for grade_distill.py. tests/evals/README.md says what they are and how to run
them.

usage: run_distill.py OUT [--runs N] [--model MODEL] [--prompt FILE] [--evals ID,..] [--parallel N]
                      [--yes]

Without --yes only the plan is printed. A run with a result.json, failed or not, is skipped, so a
batch can be resumed (delete a failed run's directory to run it again). OUT/batch.json records what
the runs are made with (the model, the command, the --prompt template if any, and a hash of each
eval's whole prompt); a batch into OUT that would make any of them differently is refused.
grade_distill.py reads batch.json to know which runs the repo's checks can grade.

A run rejected twice by validation keeps no stats, so the cost and time grade_distill.py totals
leave its two attempts out: a batch with rejections cost more than it reports.
"""

import argparse
import hashlib
import json
import os
import sys

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVALS = HERE / "distill"
SCRIPTS = HERE.parents[1] / "plugins" / "stacklet" / "skills" / "guardrails" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import distill

from _inference import InferenceError, command


def evals():
    """Every distillation eval, in id order: its eval.json plus its id, name and key."""
    out = []
    for d in sorted(EVALS.glob("[0-9]*-*/")):
        n, name = d.name.split("-", 1)
        e = json.loads((d / "eval.json").read_text(encoding="utf-8"))
        out.append({"id": int(n), "name": name, "key": d.name, **e})
    return out


def rows(e):
    """E's policies as distill.py reads them from the cache (guardrails.policy's columns)."""
    return [
        {
            "name": p["name"],
            "version": p["version"],
            "severity": p["severity"],
            "mode": p["mode"],
            "compliance": json.dumps(p.get("compliance") or []),
            "source_json": json.dumps(p["source"]),
        }
        for p in e["policies"]
    ]


def prompt_of(e, template=None):
    """The prompt distill.py would send for E's policies (with TEMPLATE in place of its PROMPT)."""
    return (template or distill.PROMPT).format(
        rtype=e["resource_type"], bundle=distill.build_bundle(rows(e))
    )


def run_one(out, e, k, cmd, template=None):
    """Run K of eval E: one distillation (with TEMPLATE as its prompt, if given), validated as
    distill.py validates one."""
    rundir = out / e["key"] / f"run-{k}"
    if (rundir / "result.json").exists():
        return f"{e['key']} {rundir.name}: done already"
    rundir.mkdir(parents=True, exist_ok=True)
    prompt = prompt_of(e, template)
    (rundir / "prompt.txt").write_text(prompt, encoding="utf-8")
    valid = {(p["name"], p["version"]) for p in e["policies"]}
    # what the model wrote, before validation: so a bullet missing from the result can be told apart
    # as one the model left out or one validation dropped
    raw = []
    try:
        model, text, stats = distill.distil_one(prompt, cmd, valid, raw=raw)
        result = {"model": model, "text": text, "stats": stats}
        msg = f"{(stats or {}).get('seconds')}s"
    except InferenceError as err:
        result, msg = {"error": str(err)}, "failed"
    except ValueError as err:
        # what distill.py would refuse to cache: graded as a failure
        result, msg = {"rejected": str(err)}, "rejected twice"
    result["raw"] = raw
    (rundir / "result.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return f"{e['key']} {rundir.name}: {msg}"


def prompt_hash(prompt):
    """What batch.json records of a whole prompt."""
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]


def settle(out, settings, prompts):
    """Record SETTINGS (what the runs are made with) and PROMPTS ({eval key: its whole prompt}) in
    OUT/batch.json, or exit if OUT holds a batch made with other settings, or with another prompt
    for any of those evals: a resumed batch must be the same batch. The whole prompt, not the
    template: a change to the bundle (build_bundle, compact) changes what the model reads as much
    as one to PROMPT. An eval OUT hasn't run yet may join."""
    path = out / "batch.json"
    hashes = {k: prompt_hash(p) for k, p in prompts.items()}
    before = {}
    if path.exists():
        before = json.loads(path.read_text(encoding="utf-8"))
        known = before.get("prompts", {})
        changed = sorted(k for k, h in hashes.items() if known.get(k, h) != h)
        if {k: v for k, v in before.items() if k != "prompts"} != settings or changed:
            sys.exit(
                f"{out} holds a batch made with other settings or prompts"
                f" ({', '.join(changed) or before}); use another OUT"
            )
    settings = {**settings, "prompts": {**before.get("prompts", {}), **hashes}}
    path.write_text(json.dumps(settings, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(
        description="Run the distillation evals; see the module docstring."
    )
    ap.add_argument("out", type=Path)
    ap.add_argument(
        "--runs", type=int, default=5, help="fresh distillations per eval (default 5)"
    )
    ap.add_argument(
        "--model", default="haiku", help="the distillation's model (default haiku)"
    )
    ap.add_argument(
        "--prompt",
        type=Path,
        help="a prompt template in place of distill.py's ({rtype} and {bundle} filled in)",
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
    template = a.prompt.read_text(encoding="utf-8") if a.prompt else None
    if template is not None and "{bundle}" not in template:
        # format() would happily drop it: every run would distil no policies at all
        sys.exit(f"--prompt {a.prompt}: no {{bundle}} in the template")
    try:
        # now, not in a worker part-way through the batch
        prompt_of(todo[0], template)
    except (KeyError, IndexError, ValueError) as err:
        sys.exit(
            f"--prompt {a.prompt}: not a template with only {{rtype}} and {{bundle}}: {err!r}"
        )
    print(
        f"{len(jobs)} distillations ({len(todo)} evals x {a.runs}), on {a.model}, "
        f"{a.parallel} at a time" + (f", prompt {a.prompt}" if a.prompt else "")
    )
    if not a.yes:
        print("add --yes to start")
        return
    # the task's model, as distill.py takes it: set in this process before the command is made, once,
    # before any thread starts (run() uses only the command); it overrides the developer's own
    os.environ["STACKLET_GUARDRAILS_DISTILL_MODEL"] = a.model
    cmd = command("distill")
    a.out.mkdir(parents=True, exist_ok=True)
    settle(
        a.out,
        {"model": a.model, "command": json.loads(cmd.recorded()), "template": template},
        {e["key"]: prompt_of(e, template) for e in todo},
    )
    with ThreadPoolExecutor(a.parallel) as ex:
        for msg in ex.map(lambda j: run_one(a.out, *j, cmd, template), jobs):
            print(msg, flush=True)
    print(f"done: python3 {HERE / 'grade_distill.py'} {a.out}")


if __name__ == "__main__":
    main()
