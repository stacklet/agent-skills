"""The cached posture, read for what needs it.

usage: posture.py DEPLOYMENT [--digest | --pending]

--digest (the default) prints the posture as text for judging scope, without policy content (see
digest); assess.py hands it to a model. Percentages are of the provider's accounts: its
all-accounts group's member count, else its active account count (not accountSummary:
docs/design.md).

--pending lists the bound collections with something to fetch, biggest first, each with `why`.

content_query builds the batched queries (platform/collection.graphql) fetching collections'
membership and content, which scope.py query prints for a repo's scope.
"""

import argparse
import json
import sys

from collections import Counter
from pathlib import Path
from typing import NamedTuple

from _cache import (
    CHECKED_POLICY_COLLECTION,
    LOADED_POLICY_COLLECTION,
    MEMBERSHIP_POSTURE,
    Cache,
    add_cache_args,
    alias_of,
)
from _filters import nesting, render_filter


# The denominator per provider: its all-accounts group's member count.
ALL_ACCOUNTS_TOTALS = """
SELECT provider, MAX(member_count) FROM account_group
WHERE dynamic_filter = ''
GROUP BY provider"""

# Each org's accounts: the member count of its generated all-accounts-in-the-org group.
ORG_TOTALS = """
SELECT provider, org, MAX(member_count) FROM account_group
WHERE org IS NOT NULL AND path IS NULL
GROUP BY provider, org"""

# Bound groups only: an unbound group carries no guardrail, and on a large estate most are unbound.
BOUND_GROUPS = """
SELECT g.uuid, g.name, g.provider, g.description, g.dynamic_filter, g.member_count
FROM account_group g
WHERE EXISTS (SELECT 1 FROM binding b WHERE b.account_group_uuid = g.uuid)
ORDER BY g.provider, g.member_count DESC, g.name, g.uuid"""


# The bindings the digest shows: those to a group the posture has (others can't be placed).
DIGEST_BINDINGS = """
SELECT b.account_group_uuid, b.collection_uuid, b.description FROM binding b
JOIN account_group g ON g.uuid = b.account_group_uuid
ORDER BY b.name"""


# Bound collections (to a group the posture has), in name order.
BOUND_COLLECTIONS = """
SELECT uuid, name, provider, description, policy_count FROM policy_collection c
WHERE EXISTS (
    SELECT 1 FROM binding b JOIN account_group g ON g.uuid = b.account_group_uuid
    WHERE b.collection_uuid = c.uuid)
ORDER BY name, uuid"""

# Bound collections whose membership is due a check, or some of whose versions lack content.
PENDING = f"""
SELECT c.uuid, c.name, c.provider, c.policy_count policies,
    CASE WHEN {MEMBERSHIP_POSTURE} IS NULL THEN 'not fetched'
         WHEN NOT {CHECKED_POLICY_COLLECTION} THEN 'fetched under an older posture'
         ELSE 'lacks content' END why
FROM policy_collection c
WHERE NOT ({LOADED_POLICY_COLLECTION} AND {CHECKED_POLICY_COLLECTION})
  AND EXISTS (SELECT 1 FROM binding b WHERE b.collection_uuid = c.uuid)
ORDER BY c.policy_count DESC, c.name"""

CONTENT_QUERY = (
    Path(__file__).resolve().parent.parent / "platform" / "collection.graphql"
)
# Policies per query: seconds to answer, well under a MB (about 2 KB a policy). Claude Code dropped
# the MCP connection somewhere between 6 and 12 MB (3,000-6,000 policies). One collection bigger than
# this gets a query alone.
BATCH = 300
# Fewer policies than this may come back inline, to be retyped, rather than as a file (Claude Code
# saves an MCP result over 25,000 tokens: about 50 policies, which vary, hence the margin).
FILE_MIN = 100

# What the digest says before the providers: how to read it.
PREAMBLE = (
    (
        "Group sizes count active accounts. A combined size is a sum of group sizes: groups may "
        "overlap."
    ),
    (
        "Each bound group is listed under the smallest other group (but no all-accounts group) "
        "whose filter holds all its accounts (by org and OU path; a further term, such as a tag, "
        "narrows it). A group whose filter doesn't say (members added by hand, tags alone) is at "
        "the top level, wherever its accounts are."
    ),
)


def digest(db):
    """The posture as text to judge scope from, without policy content: providers and orgs with
    their account counts, the bound groups with their filters in words, each under the smallest
    group holding all its accounts, and the bound collections grouped by the exact set of groups
    they are bound to, so a rollout over several groups reads as one pattern. Combined sizes are
    sums, an upper bound: groups may overlap."""
    p = read_digested(db)
    out = [
        f"# Posture of {p.meta.get('deployment')} (refreshed {p.meta.get('refreshed_at')})",
        "",
        *PREAMBLE,
    ]
    for provider in sorted({g["provider"] for g in p.groups.values()}):
        out += _provider_section(p, provider)
        out += _groups_section(p, provider)
        out += _collections_section(p, provider)
    return "\n".join(out)


class Digested(NamedTuple):
    """What the digest reads of the posture, once (read_digested)."""

    meta: dict  # the posture's meta: deployment, refreshed_at
    totals: dict  # provider -> its account count, where known (provider_totals)
    orgs: dict  # provider -> its orgs and their account counts (provider_orgs)
    org_names: dict  # org key -> name, for filters in words
    groups: dict  # bound group uuid -> its row (BOUND_GROUPS)
    nest: dict  # bound group uuid -> where it nests (_filters.nesting)
    labels: dict  # bound collection uuid -> label (collection_labels)
    glabels: dict  # bound group uuid -> label (unique_labels)
    # (provider, frozenset of group uuids) -> [collection rows] (binding_sets)
    sets: dict
    notes: dict  # collection uuid -> what its bindings say (binding_sets)


def read_digested(db):
    """The posture as the digest reads it (Digested)."""
    totals = provider_totals(db)
    groups = {g["uuid"]: g for g in db.execute(BOUND_GROUPS)}
    glabels = unique_labels(groups.values())
    sets, notes = binding_sets(db, groups, glabels)
    return Digested(
        meta=dict(db.execute("SELECT key, value FROM meta")),
        totals=totals,
        orgs=provider_orgs(db, totals),
        org_names=dict(db.execute("SELECT key, name FROM cloud_org")),
        groups=groups,
        nest=nesting(groups),
        labels=collection_labels(db),
        glabels=glabels,
        sets=sets,
        notes=notes,
    )


def provider_totals(db):
    """-> {provider: its account count}, where known."""
    totals = dict(db.execute("SELECT provider, count FROM provider_accounts"))
    totals.update(db.execute(ALL_ACCOUNTS_TOTALS))
    return totals


def provider_orgs(db, totals):
    """-> {provider: {"orgs": {org key: name}, "totals": {org key: accounts}, "standalone": accounts in
    no org (0 when TOTALS lacks the provider)}}, over orgs with accounts (stub orgs have none)."""
    names = {
        (p, k): n for p, k, n in db.execute("SELECT provider, key, name FROM cloud_org")
    }
    out = {}
    for provider, org, n in db.execute(ORG_TOTALS):
        if n:
            e = out.setdefault(provider, {"orgs": {}, "totals": {}})
            e["orgs"][org] = names.get((provider, org)) or org
            e["totals"][org] = n
    for provider, e in out.items():
        total = totals.get(provider)
        e["standalone"] = max(0, total - sum(e["totals"].values())) if total else 0
    return out


def binding_sets(db, groups, glabels):
    """-> (sets, notes): {(provider, frozenset of group uuids): [rows of the bound collections bound
    to exactly those GROUPS]}, and {collection uuid: what its bindings' descriptions add}."""
    bound = {}
    notes = {}
    for b in db.execute(DIGEST_BINDINGS):
        bound.setdefault(b["collection_uuid"], set()).add(b["account_group_uuid"])
        d = (b["description"] or "").strip()
        group = b["account_group_uuid"]
        # one naming its group only describes the group, shown already (generated ones read
        # "Compliance policies for <group> accounts"); others say why it was bound
        if d and groups[group]["name"] not in d:
            notes.setdefault(b["collection_uuid"], []).append(
                f"to {glabels[group]}: {d}"
            )
    sets = {}
    for c in db.execute(BOUND_COLLECTIONS):
        sets.setdefault((c["provider"], frozenset(bound[c["uuid"]])), []).append(c)
    return sets, notes


def _provider_section(p, provider):
    """PROVIDER's heading, with its account count, and its orgs'."""
    total = p.totals.get(provider)
    out = [
        "",
        f"## {provider}: {accounts(total) if total is not None else '? accounts'}",
    ]
    e = p.orgs.get(provider)
    if e:
        for key, name in sorted(e["orgs"].items(), key=lambda kv: -e["totals"][kv[0]]):
            label = name if name == key else f"{name} ({key})"
            out.append(f"- org {label}: {accounts(e['totals'][key])}")
        if e["standalone"]:
            out.append(f"- in no org: {accounts(e['standalone'])}")
    return out


def _groups_section(p, provider):
    """PROVIDER's bound groups, each under the smallest group holding all its accounts."""
    total = p.totals.get(provider)
    children = {}
    for g in p.groups.values():
        if g["provider"] == provider:
            children.setdefault(p.nest[g["uuid"]][0], []).append(g)
    out = ["", "### Bound account groups"]
    for g, depth in walk(children):
        text = (
            f"{'  ' * depth}- {p.glabels[g['uuid']]}: {accounts(g['member_count'])} "
            f"({pct(g['member_count'], total)}); "
            f"{render_filter(g['dynamic_filter'], p.org_names)}"
            f"{described_as(g['description'], g['name'])}"
        )
        same = p.nest[g["uuid"]][1]
        if same:
            text += f"; the same accounts as {', '.join(p.glabels[v] for v in same)}"
        out.append(text)
    return out


def walk(children, parent=None, depth=0):
    """(group, depth) for every group in CHILDREN ({parent uuid or None: [group rows]}), each
    followed by those under it."""
    for g in children.get(parent, []):
        yield g, depth
        yield from walk(children, g["uuid"], depth + 1)


def _collections_section(p, provider):
    """PROVIDER's bound collections, by the exact set of groups they are bound to: the sets reaching
    the most accounts first."""
    total = p.totals.get(provider)
    out = ["", "### Bound collections, by the groups they are bound to"]
    mine = sorted(
        ((gs, cs) for (prov, gs), cs in p.sets.items() if prov == provider),
        key=lambda x: (-sum(p.groups[u]["member_count"] for u in x[0]), -len(x[1])),
    )
    for gs, cs in mine:
        n = sum(p.groups[u]["member_count"] for u in gs)
        names = sorted(p.glabels[u] for u in gs)
        head = (
            names[0] if len(names) == 1 else f"{len(names)} groups ({', '.join(names)})"
        )
        out.append(f"- bound to {head}: combined {accounts(n)} ({pct(n, total)})")
        for c in cs:
            n = c["policy_count"]
            line = f"  - {p.labels[c['uuid']]}: {n} polic{'y' if n == 1 else 'ies'}"
            line += described_as(c["description"], c["name"])
            for note in p.notes.get(c["uuid"], ()):
                line += f"; binding {note}"
            out.append(line)
    return out


def pct(n, total):
    return f"{n / total:.0%}" if total else "?"


def accounts(n):
    return f"{n} account{'' if n == 1 else 's'}"


def described_as(d, name):
    # many descriptions only repeat the name
    return f"; described as: {d.strip()}" if d and d.strip() != name else ""


def unique_labels(rows):
    """-> {uuid: the name the digest gives it}: its name, with its uuid where another of ROWS shares
    it, so a model's answer names one."""
    rows = list(rows)
    seen = Counter(r["name"] for r in rows)
    return {
        r["uuid"]: r["name"] if seen[r["name"]] == 1 else f"{r['name']} [{r['uuid']}]"
        for r in rows
    }


def collection_labels(db):
    """-> {bound collection uuid: its label}, as the digest names it."""
    return unique_labels(db.execute(BOUND_COLLECTIONS))


def group_labels(db):
    """-> {bound group uuid: its label}, as the digest names it."""
    return unique_labels(db.execute(BOUND_GROUPS))


def pending(db):
    """Bound collections with something to fetch, biggest first."""
    return [dict(r) for r in db.execute(PENDING)]


def batches(due, spare):
    """DUE ({uuid, policies} rows, biggest first) packed first-fit into batches of up to BATCH
    policies (one bigger than that alone), the last, if too small to come back as a file, filled out
    from SPARE (other collections due a fetch): an inline result would have to be retyped, which is
    slow and can't be checked. -> lists of uuids."""

    def add(b, r):
        b[0] += r["policies"]
        b[1].append(r["uuid"])

    out = []
    for r in due:
        b = next((b for b in out if b[0] + r["policies"] <= BATCH), None)
        if b is None:
            out.append(b := [0, []])
        add(b, r)
    if not out or out[-1][0] >= FILE_MIN:
        return [b[1] for b in out]
    last = out[-1]
    for r in spare:
        if last[0] >= FILE_MIN:
            break
        if last[0] + r["policies"] <= BATCH:
            add(last, r)
    return [b[1] for b in out]


def content_query(db, uuids):
    """The collection.graphql queries for those of the collections UUIDS with something to fetch,
    batched; [] for none. Exits on a uuid the posture lacks (it would only come back null)."""
    known = {r[0] for r in db.execute("SELECT uuid FROM policy_collection")}
    wanted = set(uuids)
    unknown = wanted - known
    if unknown:
        sys.exit(f"no such collection in the posture: {', '.join(sorted(unknown))}")
    due, spare = [], []
    for r in pending(db):
        # one now empty is still due while its older membership has policies (a fetch replaces it):
        # it weighs 1
        r["policies"] = max(r["policies"], 1)
        (due if r["uuid"] in wanted else spare).append(r)
    refresh = db.execute(
        "SELECT value FROM posture.meta WHERE key = 'refresh_id'"
    ).fetchone()[0]
    template = [
        line
        for line in CONTENT_QUERY.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    queries = []
    for batch in batches(due, spare):
        lines = []
        for line in template:
            if "__POSTURE__" in line:
                lines.append(line.replace("__POSTURE__", f"posture_{refresh}"))
            elif "__UUID__" in line:
                lines += [
                    line.replace("__ALIAS__", alias_of(u)).replace("__UUID__", u)
                    for u in batch
                ]
            else:
                lines.append(line)
        queries.append("\n".join(lines))
    return queries


def format_queries(queries):
    """content_query's QUERIES as printed: each headed by a GraphQL comment, so a query can be passed
    on whole ("" for none)."""
    return "\n\n".join(
        f"# query {n} of {len(queries)}\n{q}" for n, q in enumerate(queries, 1)
    )


def main():
    # names, descriptions and filters are not ASCII-safe: e.g. a Windows cp1252 console
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    add_cache_args(ap)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--digest",
        action="store_true",
        help="the posture as text, for judging scope (the default; needs no policy content)",
    )
    mode.add_argument(
        "--pending",
        action="store_true",
        help="list bound collections with something to fetch, biggest first",
    )
    a = ap.parse_args()
    db = Cache(a.deployment).open()
    print(json.dumps(pending(db), indent=2) if a.pending else digest(db))


if __name__ == "__main__":
    main()
