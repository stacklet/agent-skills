"""Grade the distillation's eval runs (run_distill.py OUT): grade_distill.py OUT

Checks, on each run's validated distillation (what distill.py would cache and serve):
- `made`: it was made (not failed, nor rejected twice by validation);
- `cited`: every policy is cited by some bullet (validation only checks that what is cited is real);
- `no-labels`: no bullet writes the shape's slot names ("What passes:", "Compliant:");
- each of the eval's `expect`ations, by its `id`:
    {"id": "...", "policies": [NAME, ..],   the bullets citing any of these policies:
     "together": true,                      all of them in one bullet
     "linked": {NAME: [REGEX, ..], ..},     every bullet citing NAME matches each of NAME's
                                            regexes (written to match what the other
                                            policies require: their threshold, severity), so
                                            whichever bullet a reader lands on tells them all
                                            of it; this holds of a bullet citing them all too,
                                            and NAME's own ref (SELF) and the others' (REF)
                                            don't count as saying it
     "apart": true,                         no two of them in one bullet
     "says": [REGEX, ..],                   each matches those bullets (case-insensitive), each
                                            ref in them replaced by REF (a name is no saying)
     "not": [REGEX, ..],                    none matches them
     "runtime": true | false}               they are (aren't) under the runtime-only heading
A check about bullets fails when no bullet cites its policies (that is `cited`'s failure too).

Each eval may hold `controls`: outputs with the checks they must fail (`fails`: ids, [] for none),
which tests/test_run_distill.py checks, so a check is known to tell a good output from a bad one.

The checks are always the repo's current ones, so fixing a check needs no new runs. They grade only
the evals whose runs read the prompt the repo builds for them now (with the batch's --prompt
template, if it had one: OUT/batch.json); an eval whose policies changed since, or that is gone, is
named and left out, never graded by checks written for other inputs. Writes OUT/EVAL/grading.json
and prints, per eval, each check's passes over its runs, then the totals and what the runs took.
"""

import json
import re
import sys

from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(
    0,
    str(HERE.parents[1] / "plugins" / "stacklet" / "skills" / "guardrails" / "scripts"),
)

import run_distill

from distill import RUNTIME_ONLY, TOKEN, parse_blocks, refs_in


# the shape's slots written as labels; "flagged:" in a sentence would be prose
LABELS = re.compile(
    r"\b(what passes|what is flagged|non-compliant|compliant)\s*:", re.IGNORECASE
)
GENERAL = ("made", "cited", "no-labels")


def bullets(text, valid):
    """(bullet line, the refs it cites, whether it is under the runtime-only heading) for each bullet
    in TEXT, found and read exactly as distill.py's validation finds and reads them (parse_blocks,
    refs_in), so the grader and validation agree on what a bullet is and what it cites."""
    out, runtime = [], False
    for kind, line in parse_blocks(text):
        if kind == "heading" and line.strip() == RUNTIME_ONLY:
            runtime = True
        elif kind == "bullet":
            out.append((line, set(refs_in(line, valid)[0]), runtime))
    return out


def citing(bs, refs):
    """The bullets in BS citing any of REFS ("name@version")."""
    return [b for b in bs if b[1] & set(refs)]


def said(line, own=None):
    """LINE with each ref replaced by `REF`: a policy's name ("...-remove@1") is no saying, while a ref
    can still stand as a sentence's subject ("REF sets it back"). OWN (a ref) becomes `SELF`, so that
    `REF` is only ever another policy: a bullet's own ref names nothing else."""
    if own:
        line = re.sub(re.escape(own) + r"(?![0-9])", "SELF", line)
    return TOKEN.sub("REF", line)


def check_expect(x, bs, refs_of):
    """(passed, why not) for expectation X over bullets BS."""
    refs = [refs_of[n] for n in x["policies"]]
    cited = citing(bs, refs)
    if not cited:
        return False, "no bullet cites them"
    joined = any(set(refs) <= b[1] for b in cited)
    if x.get("together") and not joined:
        return False, "not in one bullet"
    for name, pats in x.get("linked", {}).items():
        for b in citing(bs, [refs_of[name]]):
            own = said(b[0], refs_of[name])
            for pat in pats:
                if not re.search(pat, own, re.IGNORECASE):
                    return (
                        False,
                        f"a bullet citing {name} doesn't say what the others require: /{pat}/",
                    )
    if x.get("apart") and any(len(set(refs) & b[1]) > 1 for b in cited):
        return False, "two in one bullet"
    text = "\n".join(said(b[0]) for b in cited)
    for pat in x.get("says", []):
        if not re.search(pat, text, re.IGNORECASE | re.MULTILINE):
            return False, f"doesn't say /{pat}/"
    for pat in x.get("not", []):
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            return False, f"says /{pat}/: {m.group(0)!r}"
    if "runtime" in x and any(b[2] != x["runtime"] for b in cited):
        if x["runtime"]:
            return False, "not under the runtime-only heading"
        return False, "under the runtime-only heading"
    return True, ""


def grade(e, result):
    """{check id: (passed, why not)} for one run's RESULT of eval E."""
    text = result.get("text")
    if not text:
        why = result.get("error") or result.get("rejected") or "no output"
        return {
            c: (False, why) for c in (*GENERAL, *(x["id"] for x in e.get("expect", [])))
        }
    valid = {(p["name"], p["version"]) for p in e["policies"]}
    refs_of = {p["name"]: f"{p['name']}@{p['version']}" for p in e["policies"]}
    bs = bullets(text, valid)
    uncited = [n for n, r in refs_of.items() if not citing(bs, [r])]
    labelled = [b[0] for b in bs if LABELS.search(b[0])]
    out = {
        "made": (True, ""),
        "cited": (not uncited, f"uncited: {', '.join(uncited)}"),
        "no-labels": (not labelled, labelled[0] if labelled else ""),
    }
    for x in e.get("expect", []):
        out[x["id"]] = check_expect(x, bs, refs_of)
    return out


def took(results):
    """What RESULTS took, from their stats (_inference.combined's): mean seconds, total cost where
    the default command reported it, and how many needed a second attempt."""
    stats = [r["stats"] for r in results if r.get("stats")]
    if not stats:
        return "-"
    parts = [f"{sum(s.get('seconds', 0) for s in stats) / len(stats):.0f}s a run"]
    costs = [s["cost_usd"] for s in stats if "cost_usd" in s]
    if costs:
        parts.append(f"${sum(costs):.2f} in all")
    retried = sum(s.get("attempts", 1) > 1 for s in stats)
    if retried:
        parts.append(f"{retried} retried")
    return ", ".join(parts)


def gradeable(batch):
    """The repo's evals whose runs in BATCH (OUT/batch.json) read the prompt the repo builds for them
    now, with the batch's template: -> (those evals, {key: why not} for the rest of the batch's)."""
    repo = {e["key"]: e for e in run_distill.evals()}
    template = batch.get("template")
    out, skipped = [], {}
    for key, h in sorted(batch.get("prompts", {}).items()):
        now = repo.get(key)
        if now is None:
            skipped[key] = "no longer in the repo"
        elif run_distill.prompt_hash(run_distill.prompt_of(now, template)) != h:
            skipped[key] = (
                "its runs read another prompt than the repo builds for it now"
            )
        else:
            out.append(now)
    return out, skipped


def main(out):
    out = Path(out)
    evals, skipped = gradeable(
        json.loads((out / "batch.json").read_text(encoding="utf-8"))
    )
    for key, why in skipped.items():
        print(f"not graded: {key}: {why}")
    total = {}
    for e in evals:
        runs = sorted(
            (out / e["key"]).glob("run-*/result.json"),
            key=lambda p: int(p.parent.name.split("-")[1]),
        )
        if not runs:
            continue
        results = [json.loads(p.read_text(encoding="utf-8")) for p in runs]
        graded = [grade(e, r) for r in results]
        checks = list(graded[0])
        report = {
            c: {
                "passed": sum(g[c][0] for g in graded),
                "runs": len(graded),
                "failures": [
                    {"run": p.parent.name, "why": g[c][1]}
                    for p, g in zip(runs, graded)
                    if not g[c][0]
                ],
            }
            for c in checks
        }
        (out / e["key"] / "grading.json").write_text(
            json.dumps(report, indent=1), encoding="utf-8"
        )
        print(f"{e['key']}: {took(results)}")
        for c in checks:
            r = report[c]
            line = f"  {r['passed']}/{r['runs']} {c}"
            if r["failures"]:
                line += (
                    f"  ({r['failures'][0]['run']}: {r['failures'][0]['why'][:120]})"
                )
            print(line)
            t = total.setdefault("general" if c in GENERAL else "expected", [0, 0])
            t[0] += r["passed"]
            t[1] += r["runs"]
    for kind, (p, n) in total.items():
        print(f"{kind}: {p}/{n} checks passed")


if __name__ == "__main__":
    main(sys.argv[1])
