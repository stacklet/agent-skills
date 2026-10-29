"""Grade the posture assessment's eval runs (run_assess.py OUT): grade_assess.py OUT

Checks, against the posture each run assessed (its posture.json):
- per run: it was made; nothing that holds policies and is broad (bound to a group of a whole org,
  or reaching at least BROAD of its provider's accounts) is ignored; nothing that "applies" reaches
  less than NARROW of them, and nothing reaching NEAR or more is merely asked about; and the eval's
  `expect`ations hold.
- across an eval's runs: developers get the scope most runs give them (see below) in at least AGREE
  of runs, weighted by accounts, over the groups whose scope questions decide.
- the reassessment was made and keeps every question of the run it reassessed, id and wording.

Scope is judged by outcome, not by how questions are worded or grouped: for each bound group G, a
developer whose code deploys into G answers yes to every question naming G or a group holding G,
unless it also names a part of G (it asks about that part, which they may not be in), and no to
opt-outs, with no account looked up; their scope is what applies plus what those yeses add.
Agreement counts only the groups whose scope differs from what applies in at least one run: on the
others (all-accounts and whole-org groups, mostly) every run trivially agrees, and weighting them by
accounts would drown a disagreement about narrower groups; with no such group, runs agree. Two
measures come from that too, reported rather than checked (they rest on knowing which groups hold
which, and only org and OU-path filters say): imprecise questions (a yes adding collections bound to
no group holding G) and coverage gaps ("ask" collections bound to a group holding G that no yes of
G's developer adds, counting only G's accounts that no part of G brings it to). Shares under NOTED
of the accounts are left out of both.

`expect` (eval.json), by collection name as the posture gives it:
  {"collections": {"NAME": ["applies", "ask"], ..},   the defaults it may have
   "questions": [{"adds": ["NAME", ..]}, {"removes": ["NAME", ..]}]}  some question's yes adds
                                                       (removes) at least these
Writes OUT/EVAL/grading.json and prints a summary per eval, with what the runs took.
"""

import json
import re
import sys

from collections import Counter
from pathlib import Path


# which groups hold which: the digest's reading of filters, so grading and the model see the same
sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / "plugins/stacklet/skills/guardrails/scripts"
    ),
)
from _filters import disjoint, holds, region


BROAD = 0.5  # ignoring what reaches this share of a provider's accounts is a fault
NARROW = 0.25  # "applies" to less than this share is a fault (bound to all accounts is a rule's)
NEAR = 0.95  # only asking about what surely reaches this share is a fault: it applies
AGREE = 0.9  # agreement on scope across runs (account-weighted) that passes
NOTED = 0.01  # groups smaller than this share of accounts are left out of the measures


class Posture:
    """What grading needs of a posture response: names, sizes, bindings, reach, and which bound
    groups hold which."""

    def __init__(self, posture):
        data = posture["data"]
        bp = data["bindingPosture"]
        self.groups = {g["uuid"]: g for g in bp["accountGroups"]}
        self.totals = {}
        for g in self.groups.values():
            if g["dynamicFilter"] == "":
                n = g["accountMappings"]["pageInfo"]["total"]
                self.totals[g["provider"]] = max(self.totals.get(g["provider"], 0), n)
        self.names = {c["uuid"]: c["name"] for c in bp["policyCollections"]}
        self.provider = {c["uuid"]: c["provider"] for c in bp["policyCollections"]}
        self.policies = {
            c["uuid"]: c["policyMappings"]["pageInfo"]["total"]
            for c in bp["policyCollections"]
        }
        self.bound = {}  # collection -> its groups
        self.on = {}  # group -> its collections
        for b in bp["bindings"]:
            if b["lastDeployed"]:
                c, g = b["policyCollection"]["uuid"], b["accountGroup"]["uuid"]
                self.bound.setdefault(c, []).append(self.groups[g])
                self.on.setdefault(g, set()).add(c)
        self.regions = regions = {
            u: region(g["dynamicFilter"]) for u, g in self.groups.items()
        }
        self.reach = {}  # collection -> (share or None, org-wide)
        # collection -> share it surely reaches: its groups' sizes summed only over groups that
        # can't overlap (a sum over all of them is an upper bound, and nested groups inflate it)
        self.surely = {}
        for u, gs in self.bound.items():
            total = self.totals.get(gs[0]["provider"])
            n = sum(g["accountMappings"]["pageInfo"]["total"] for g in gs)
            org_wide = any(org_only(g["dynamicFilter"]) for g in gs)
            everyone = any(g["dynamicFilter"] == "" for g in gs)
            self.reach[u] = (
                1.0 if everyone else (n / total if total else None),
                org_wide or everyone,
            )
            apart = []
            for g in sorted(
                gs, key=lambda g: -g["accountMappings"]["pageInfo"]["total"]
            ):
                if all(disjoint(regions[g["uuid"]], regions[h["uuid"]]) for h in apart):
                    apart.append(g)
            m = sum(g["accountMappings"]["pageInfo"]["total"] for g in apart)
            self.surely[u] = 1.0 if everyone else (m / total if total else 0)
        # the bound groups each bound group's accounts are all in, itself included
        self.within = {
            u: {u}
            | {
                v
                for v in self.on
                if self.groups[v]["provider"] == self.groups[u]["provider"]
                and holds(regions[v], regions[u])
            }
            for u in self.on
        }

    def share(self, g):
        total = self.totals.get(g["provider"])
        return g["accountMappings"]["pageInfo"]["total"] / total if total else 0

    def scope(self, a, gid):
        """The scope a developer deploying into group GID ends up with under assessment A."""
        mine = self.within[gid]
        s = {u for u, c in a["collections"].items() if c["default"] == "applies"}
        for q in a["questions"]:
            into = set(q.get("into", []))
            # a question naming a part of GID (held by it, not holding it: groups of the same
            # accounts hold each other) asks about that part, which GID's developer may not deploy
            # into: only one naming GID or what holds it is surely a yes
            if mine & into and not any(
                gid in self.within[v] and v not in mine
                for v in into
                if v in self.within
            ):
                s |= set(q["add"])
        p = self.groups[gid]["provider"]
        return frozenset(u for u in s if self.provider.get(u) == p)


def org_only(f):
    """Whether filter F selects a whole org (the platform's per-org group), not part of one."""
    try:
        t = json.loads(f or "")
    except ValueError:
        return bool(f) and f.startswith("org:") and " path:" not in f
    s = t.get("single") if isinstance(t, dict) else None
    return isinstance(s, dict) and s.get("name") == "org"


def run_checks(result, p, expect):
    """[(text, passed)] for one run's RESULT against Posture P."""
    a = result.get("assessment")
    if not a:
        return [("Assessment made", False)]
    cols, names, reach = a["collections"], p.names, p.reach
    out = [("Assessment made", True)]

    def listed(text, found):
        return (text + (f": {', '.join(found)}" if found else ""), not found)

    out.append(
        listed(
            f"Nothing broad (a whole org, or >= {BROAD:.0%}) ignored",
            sorted(
                names[u]
                for u, c in cols.items()
                if c["default"] == "ignore"
                and p.policies.get(u)
                and u in reach
                and (reach[u][1] or (reach[u][0] or 0) >= BROAD)
            ),
        )
    )
    out.append(
        listed(
            f"Nothing narrow (< {NARROW:.0%}) applies",
            sorted(
                names[u]
                for u, c in cols.items()
                if c["default"] == "applies"
                and u in reach
                and not reach[u][1]
                and reach[u][0] is not None
                and reach[u][0] < NARROW
            ),
        )
    )
    out.append(
        listed(
            f"Nothing reaching >= {NEAR:.0%} only asked about",
            sorted(
                names[u]
                for u, c in cols.items()
                if c["default"] == "ask"
                and p.policies.get(u)
                and p.surely.get(u, 0) >= NEAR
            ),
        )
    )
    by_name = {n: u for u, n in names.items()}
    for name, allowed in (expect.get("collections") or {}).items():
        got = cols.get(by_name.get(name), {}).get("default")
        out.append((f"{name}: {' or '.join(allowed)}", got in allowed))
    for q in expect.get("questions") or []:
        verb = "adds" if "adds" in q else "removes"
        want = {by_name.get(n) for n in q[verb]}
        field = "add" if verb == "adds" else "remove"
        ok = any(want <= set(x[field]) for x in a["questions"])
        out.append((f"Some question's yes {verb} {', '.join(q[verb])}", ok))
    return out


def top(counts, n=3):
    """COUNTS' N most common names, ties by name: the report must come out the same every time."""
    return ", ".join(
        k for k, _ in sorted(counts.items(), key=lambda i: (-i[1], i[0]))[:n]
    )


def covered(a, p, gid, u):
    """The share of group GID's accounts whose developers get collection U under assessment A
    through a part of GID (a bound group inside it), counting only parts that can't overlap."""
    parts = sorted(
        (v for v in p.on if gid in p.within[v] and v not in p.within[gid]),
        key=lambda v: -p.share(p.groups[v]),
    )
    apart = []
    for v in parts:
        if u in p.scope(a, v) and all(
            disjoint(p.regions[v], p.regions[w]) for w in apart
        ):
            apart.append(v)
    return sum(p.share(p.groups[v]) for v in apart)


def run_measures(result, p):
    """[text] for one run: its imprecise questions and coverage gaps (see the module docstring)."""
    a = result.get("assessment")
    if not a:
        return []
    applies = {u for u, c in a["collections"].items() if c["default"] == "applies"}
    asks = {u for u, c in a["collections"].items() if c["default"] == "ask"}
    extra, gaps = Counter(), Counter()
    worst = [0.0, 0.0]
    for gid in p.on:
        mine = {u for v in p.within[gid] for u in p.on[v] if p.policies.get(u)}
        got = p.scope(a, gid)
        e, g = (
            got - applies - mine - {u for u in got if not p.policies.get(u)},
            (mine & asks) - got,
        )
        share = p.share(p.groups[gid])
        if share < NOTED:
            continue
        if e:
            extra.update(p.names[u] for u in e)
            worst[0] = max(worst[0], share)
        for u in g:
            # only the accounts no part of the group brings it to: a developer in a part of
            # GID (an OU of a wider group, say) may answer yes to a question about that part
            left = share - covered(a, p, gid, u)
            if left >= NOTED:
                gaps[p.names[u]] += 1
                worst[1] = max(worst[1], left)
    out = []
    if extra:
        out.append(
            f"imprecise: a yes adds collections bound to none of their groups for developers in "
            f"groups of up to {worst[0]:.0%} of accounts, e.g. {top(extra)}"
        )
    if gaps:
        out.append(
            f"coverage gap: no yes adds these 'ask' collections for developers in up to "
            f"{worst[1]:.0%} of accounts: {top(gaps)}"
        )
    return out


def stable(results, p):
    """([(text, passed)], [measure text]) across an eval's runs: how often a developer in each group
    whose scope questions decide (in some run, not just what applies) gets the scope most runs give
    them, weighted by the group's share of its provider's accounts."""
    made = [r["assessment"] for r in results if r.get("assessment")]
    if len(made) < 2:
        return [], []
    applies = [
        {u for u, c in a["collections"].items() if c["default"] == "applies"}
        for a in made
    ]
    weight = agreed = 0.0
    differ = Counter()
    for gid in p.on:
        provider = p.groups[gid]["provider"]
        scopes = [p.scope(a, gid) for a in made]
        if all(
            s == {u for u in ap if p.provider.get(u) == provider}
            for s, ap in zip(scopes, applies)
        ):
            continue
        w = p.share(p.groups[gid])
        weight += w
        agreed += w * Counter(scopes).most_common(1)[0][1] / len(made)
        if len(set(scopes)) > 1:
            differ.update(
                p.names[u]
                for u in frozenset.union(*scopes) - frozenset.intersection(*scopes)
            )
    share = agreed / weight if weight else 1.0
    checks = [
        (
            f"Runs agree on developers' scope >= {AGREE:.0%} ({share:.0%})"
            + (f"; differs on {top(differ)}" if differ else ""),
            share >= AGREE,
        )
    ]
    flips = sorted(
        p.names[u]
        for u in made[0]["collections"]
        if len({a["collections"].get(u, {}).get("default") for a in made}) > 1
    )
    measures = [f"defaults differ between runs on: {', '.join(flips)}"] if flips else []
    return checks, measures


def kept(first, again):
    """[(text, passed)]: the reassessment was made and kept the first run's questions, id and
    wording. Nothing to check when the first run made no assessment."""
    if not first.get("assessment"):
        return []
    if again.get("rc") or not again.get("assessment"):
        return [(f"Reassessment made (rc={again.get('rc')})", False)]
    before = {(q["id"], q["ask"]) for q in first["assessment"]["questions"]}
    after = {(q["id"], q["ask"]) for q in again["assessment"]["questions"]}
    return [
        (
            f"Reassessment kept every question ({len(before & after)}/{len(before)})",
            before <= after,
        )
    ]


def took(results):
    stats = [r.get("stats") or {} for r in results if r.get("assessment")]
    secs = [s.get("seconds", 0) for s in stats]
    tokens = [s.get("input_tokens", 0) + s.get("output_tokens", 0) for s in stats]
    cost = sum(s.get("cost_usd") or 0 for s in stats)
    if not secs:
        return "no runs made"
    return f"{min(secs):.0f}-{max(secs):.0f}s, ~{sum(tokens) // len(tokens):,} tokens, ${cost:.2f}"


def main(out):
    out = Path(out)
    specs = {
        e["key"]: e
        for e in json.loads((out / "evals.json").read_text(encoding="utf-8"))
    }
    totals = [0, 0]
    for key, e in specs.items():
        runs = sorted(
            (int(re.sub(r"\D", "", r.parent.name)), r)
            for r in (out / key).glob("run-*/result.json")
        )
        if not runs:
            continue
        p = Posture(
            json.loads((runs[0][1].parent / "posture.json").read_text(encoding="utf-8"))
        )
        results = [json.loads(r.read_text(encoding="utf-8")) for _, r in runs]
        checks, measures = [], []
        # numbered as their directories are, even with one deleted to be made again
        for i, r in zip((n for n, _ in runs), results):
            checks += [
                (f"run {i}: {t}", ok)
                for t, ok in run_checks(r, p, e.get("expect") or {})
            ]
            measures += [f"run {i}: {m}" for m in run_measures(r, p)]
        across, also = stable(results, p)
        checks += across
        measures += also
        # the reassessment is of run 1's posture and assessment, whichever runs are here
        again = out / key / "reassess" / "result.json"
        first = dict(runs).get(1)
        if again.exists() and first:
            checks += kept(
                json.loads(first.read_text(encoding="utf-8")),
                json.loads(again.read_text(encoding="utf-8")),
            )
        passed = sum(ok for _, ok in checks)
        totals[0] += passed
        totals[1] += len(checks)
        (out / key / "grading.json").write_text(
            json.dumps(
                {
                    "checks": [{"text": t, "passed": ok} for t, ok in checks],
                    "measures": measures,
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"{key:40s} {passed}/{len(checks)}  {took(results)}")
        for t, ok in checks:
            if not ok:
                print(f"    FAIL {t}")
        for m in measures:
            print(f"    note {m}")
    print(f"\nassessment evals: checks {totals[0]}/{totals[1]}")


if __name__ == "__main__":
    main(sys.argv[1])
