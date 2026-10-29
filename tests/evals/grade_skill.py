"""Programmatic grader for eval iteration dirs: runs each eval's checks.py (tests/evals/skill/NN-name/)
on the generated code, the reply and the transcript.

usage: grade_skill.py ITERATION_DIR
Layout: eval-N-name/{with_skill,without_skill}/run-K/outputs. Writes each run's grading.json (its
checks, and ungraded measures of its last reply: words, out-of-scope sentences, chained script
calls) and prints totals per kind of eval (discriminating, guard, flow), never added together.
"""

import importlib.util
import json
import sys

from functools import cache
from pathlib import Path


EVALS = Path(__file__).resolve().parent / "skill"
sys.path.insert(0, str(EVALS))
# grading's fixture uuids
sys.path.insert(0, str(EVALS.parent.parent / "fixture"))

from grading import (
    chained_script_calls,
    final,
    first_edit,
    has,
    last_reply,
    looked_up,
    sentences,
    tf,
    tool_calls,
)


def evals():
    """Every eval, in id order: its eval.json, plus the id and name its directory gives it."""
    out = []
    for d in sorted(EVALS.glob("[0-9]*-*/")):
        n, name = d.name.split("-", 1)
        e = json.loads((d / "eval.json").read_text(encoding="utf-8"))
        out.append({"id": int(n), "name": name, "dir": d, **e})
    return out


@cache
def module(name):
    """Eval NAME's checks.py, or None for an eval that no longer exists."""
    for e in evals():
        if e["name"] == name:
            spec = importlib.util.spec_from_file_location(
                f"checks_{e['id']:02d}", e["dir"] / "checks.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def process_checks(mod, d):
    """What the agent did, from the run's transcript: checks no reply or code can show. Runs without
    a transcript (made some other way) skip them."""
    calls = tool_calls(d)
    if calls is None:
        return []
    arm = d.parent.parent.name
    out = []
    if arm != "without_skill":
        edit = first_edit(calls)
        for rt in getattr(mod, "SHOWS", ()):
            shown = looked_up(calls, rt)
            out.append(
                (
                    f"Looked up {rt} with distill.py show before editing",
                    bool(shown) and (edit is None or shown[0] < edit),
                )
            )
    if hasattr(mod, "process_checks"):
        out += mod.process_checks(calls, arm)
    return out


def server_log(d):
    """The calls a flow eval's fake Stacklet server logged for the run whose outputs are in D (its
    mcp_log.jsonl, beside them), in order: fake_stacklet_mcp.py's docstring says what each holds."""
    p = d.parent / "mcp_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]


def cache_state(d):
    """What a flow eval's run left in its cache (run_skill.cache_state), or an empty state."""
    p = d.parent / "cache_state.json"
    if not p.exists():
        return {"refresh_id": None, "memberships": {}, "accounts": []}
    return json.loads(p.read_text(encoding="utf-8"))


def checks(name, d):
    """[(text, passed)] for eval NAME's run whose outputs are in D."""
    mod = module(name)
    if mod is None:
        return []
    t, f = tf(d), final(d)
    out = mod.checks(d, t, f) if hasattr(mod, "checks") else []
    out += process_checks(mod, d)
    if hasattr(mod, "server_checks"):
        # a flow eval's: what its fake server and cache say, with or without a transcript
        out += mod.server_checks(
            server_log(d), cache_state(d), tool_calls(d, sub=True) or []
        )
    return out


KINDS = {e["name"]: e.get("kind", "discriminating") for e in evals()}


def out_of_scope_sentences(name, f):
    """Sentences of F about collections that don't apply here: noise, measured rather than graded.
    An eval on another fixture says what is out of scope for it (its OUT_OF_SCOPE regex)."""
    mod = module(name)
    rx = getattr(mod, "OUT_OF_SCOPE", None)
    if rx is None:
        rx = r"\bSandbox Tagging\b|\bOwner\b tag"
        if not getattr(mod, "PCI_IN_SCOPE", False):
            rx += r"|\bPCI\b|card ?data|cardholder"
    return sum(has(rx, x) for x in sentences(f))


def main(iteration):
    totals = {}
    for evald in sorted(Path(iteration).glob("eval-*")):
        key = evald.name.split("-", 2)[2]
        for cfg in ("with_skill", "without_skill"):
            for rundir in sorted((evald / cfg).glob("run-*")):
                out = rundir / "outputs"
                if not out.exists():
                    continue
                exps = [
                    {
                        "text": t,
                        "passed": bool(p),
                        "evidence": "programmatic check on output files",
                    }
                    for t, p in checks(key, out)
                ]
                # an eval since removed or renamed: nothing to grade it by
                if not exps:
                    print(
                        f"{key}: no checks (eval gone?), {rundir} not graded",
                        file=sys.stderr,
                    )
                    continue
                n = sum(e["passed"] for e in exps)
                # measured, not graded: a word cap is arbitrary, but replies growing is worth seeing
                reply = last_reply(out)
                words = len(reply.split())
                noise = out_of_scope_sentences(key, reply)
                calls = tool_calls(out)
                chained = None if calls is None else chained_script_calls(calls)
                t = totals.setdefault((KINDS[key], cfg), [0, 0, 0, 0, 0, 0])
                for i, v in enumerate((n, len(exps), n == len(exps), 1, words, noise)):
                    t[i] += v
                (rundir / "grading.json").write_text(
                    json.dumps(
                        {
                            "expectations": exps,
                            "summary": {
                                "passed": n,
                                "failed": len(exps) - n,
                                "total": len(exps),
                                "pass_rate": n / len(exps),
                                "reply_words": words,
                                "out_of_scope_sentences": noise,
                                "chained_script_calls": chained,
                            },
                        },
                        indent=1,
                    )
                )
                print(
                    f"{key:33s} {cfg:13s} {rundir.name} {n}/{len(exps)} {words:4d} words"
                    f" {noise} off-scope"
                    + ("" if chained is None else f" {chained} chained")
                )

    # Discriminating evals and regression guards answer different questions; never add them up.
    print()
    for (kind, cfg), (p, t, clean, runs, words, noise) in sorted(totals.items()):
        print(
            f"{kind:14s} {cfg:13s} checks {p}/{t}, clean runs {clean}/{runs},"
            f" avg {words // runs} words, {noise / runs:.1f} off-scope sentences/run"
        )


if __name__ == "__main__":
    main(sys.argv[1])
