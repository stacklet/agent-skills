"""Load what an agent fetched into the cache: one command per kind of data.

usage: ingest.py DEPLOYMENT posture FILE [ACTIVE_ACCOUNTS ..]
       ingest.py DEPLOYMENT collections FILE [FILE ..]
       ingest.py DEPLOYMENT accounts FILE [FILE ..]

`posture` builds posture.db afresh from a posture.graphql response and swaps it in (a failed
refresh keeps the old one); active_accounts.graphql responses after it give the account totals of
providers without an all-accounts group. `collections` stores collection.graphql responses (as
scope.py query prints the queries): each collection's membership, replacing its last, and the
content of the versions it binds. `accounts` stores account.graphql lookups, each replacing that
account's last. All but `posture` skip (and say) only what they can't read. A response from any
deployment but DEPLOYMENT (its `platform.urls.console`) is refused: it came through the wrong
Stacklet MCP server. The two dbs: docs/design.md.
"""

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import uuid

from pathlib import Path

from _cache import (
    POSTURE_LAYOUT,
    Cache,
    add_cache_args,
    alias_of,
    deployment_name,
    now_utc,
)
from _filters import region


# The posture db; bump _cache.POSTURE_LAYOUT with any change.
SCHEMA = """
-- org/path: only for the platform's generated org filters (org_scope); NULL for anything else
CREATE TABLE account_group (uuid TEXT PRIMARY KEY, name, provider, description,
    dynamic_filter, member_count INT, org TEXT, path TEXT);
CREATE TABLE cloud_org (provider TEXT, key TEXT, name, PRIMARY KEY (provider, key));
CREATE TABLE provider_accounts (provider TEXT PRIMARY KEY, count INT);  -- active accounts: fallback denominator
CREATE TABLE policy_collection (uuid TEXT PRIMARY KEY, name, provider, description,
    policy_count INT);
CREATE TABLE binding (uuid TEXT PRIMARY KEY, name, description, last_deployed,
    account_group_uuid, collection_uuid);
-- refreshed_at: when posture.json was ingested; refresh_id: unique to that ingest (a membership records
-- the one it was fetched under); deployment: the cache name of console, the URL it came from
CREATE TABLE meta (key PRIMARY KEY, value);
"""


def jlist(v):
    """A policy's compliance (a list, null, or a string: one value) -> a sorted JSON list without
    repeats."""
    if not v:
        return "[]"
    if not isinstance(v, (list, tuple)):
        v = [v]
    return json.dumps(sorted({str(i) for i in v}))


def policy_row(p):
    """A collection.graphql policy -> its guardrails.policy row. Raises on one it can't store."""
    if not isinstance(p.get("source"), str):
        raise TypeError(f"policy {p.get('name')!r}: no source")
    return (
        p["uuid"],
        int(p["version"]),
        p["name"],
        p["provider"],
        p["resourceType"],
        p["mode"],
        p["severity"],
        jlist(p["compliance"]),
        p["source"],
    )


def read_collections(path, deployment):
    """({alias: collection}, the refresh id the query was made under, {alias: error}) from the
    collection.graphql response at PATH, which must be DEPLOYMENT's. A collection is None where the
    platform has none of that uuid or failed it alone, so one it can't serve never holds back its
    batch, and so is one whose alias isn't its uuid's (alias_of): its membership would be stored
    under another collection than the one ingest names. Raises on what it can't read, on an error not
    about one alias, and on a response without exactly one posture alias: it would count as checked
    under whatever posture is current, or one of several."""
    data, errors = response_data(path, partial=True)
    console_of(data, deployment)
    refreshes = [k[len("posture_") :] for k in data if k.startswith("posture_")]
    if len(refreshes) != 1:
        found = (
            f"{len(refreshes)} posture_<id> aliases"
            if refreshes
            else "no posture_<id> alias"
        )
        raise ValueError(f"{found}: fetch with the queries scope.py query prints")
    refresh = refreshes[0]
    collections = {
        k: v
        for k, v in data.items()
        if k != "platform" and not k.startswith("posture_")
    }
    failed = {}
    for err in errors:
        where = (err.get("path") or [None])[0] if isinstance(err, dict) else None
        # a collection's alias, even one missing from data
        if (
            not isinstance(where, str)
            or where == "platform"
            or where.startswith("posture_")
        ):
            raise ValueError(
                f"the platform reported errors: {json.dumps(errors)[:500]}"
            )
        collections.setdefault(where, None)
        failed[where] = str(err.get("message", err))[:200]
    for alias, c in collections.items():
        if c and alias not in failed and alias_of(str(c.get("uuid"))) != alias:
            failed[alias] = f"its alias isn't its uuid's ({c.get('uuid')})"
    return collections, refresh, failed


def load_collection(db, c, skipped, refresh):
    """Replace collection C's membership snapshot and add the content of the versions it binds.
    -> whether all of that content was stored. A policy whose content can't be stored (no `source`,
    say) goes in SKIPPED and stays in the membership, so the collection reads as not loaded rather
    than as fewer guardrails; one that can't be placed in the membership at all (no uuid or version)
    raises, keeping the last membership. REFRESH is the posture the query was made under. Raises,
    storing nothing, on a truncated membership or one fetched under an older posture than the one
    cached: the write lock is taken first and the current posture read afresh, since a refresh may
    have swapped posture.db after this connection attached it. The caller commits."""
    db.execute("BEGIN IMMEDIATE")
    path = next(r[2] for r in db.execute("PRAGMA database_list") if r[1] == "posture")
    fresh = sqlite3.connect(path)
    try:
        current = fresh.execute(
            "SELECT value FROM meta WHERE key = 'refresh_id'"
        ).fetchone()[0]
    finally:
        fresh.close()
    if (
        refresh != current
        and db.execute(
            "SELECT 1 FROM main.membership WHERE collection_uuid = ? AND posture = ?",
            (c["uuid"], current),
        ).fetchone()
    ):
        raise ValueError(
            "fetched under an older posture than the membership already cached, which is kept"
        )
    m = c["policyMappings"]
    edges, n = m["edges"], m["pageInfo"]["total"]
    if m["pageInfo"]["hasNextPage"] or len(edges) != n:
        raise ValueError(
            f"{len(edges)} of its {n} policies returned; raise `first:` "
            "for policyMappings in platform/collection.graphql"
        )
    policies = [(e.get("node") or {}).get("policy") for e in edges]
    if not all(policies):
        # one the platform won't show: its guardrail would be silently missing
        raise ValueError("a policy came back null")
    uuids = [p["uuid"] for p in policies]
    if len(set(uuids)) != len(uuids):
        # a collection binds one version of a policy: two would collapse into one membership row
        raise ValueError("a policy appears twice")
    db.execute(
        "DELETE FROM main.collection_policy WHERE collection_uuid = ?", (c["uuid"],)
    )
    db.executemany(
        "INSERT INTO main.collection_policy VALUES (?,?,?)",
        [(c["uuid"], p["uuid"], int(p["version"])) for p in policies],
    )
    complete = True
    for p in policies:
        try:
            row = policy_row(p)
        except (KeyError, TypeError, ValueError) as e:
            skipped.append(f"{type(e).__name__}: {e}")
            complete = False
            continue
        # OR IGNORE: versions are immutable, so one already present is the same content
        db.execute("INSERT OR IGNORE INTO main.policy VALUES (?,?,?,?,?,?,?,?,?)", row)
    db.execute(
        "INSERT OR REPLACE INTO main.membership VALUES (?, ?)", (c["uuid"], refresh)
    )
    return complete


def response_data(path, partial=False):
    """The data of the GraphQL response saved at PATH (bare, under "data", or in the MCP tool's
    wrapper). Raises with the platform's errors if it reported any: a partial posture would quietly
    mean fewer guardrails. With PARTIAL, -> (data, errors), for a caller that can tell which parts
    they spoil."""
    r = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(r, dict):
        raise TypeError("not a GraphQL response")
    errors = r.get("errors") or []
    data = r.get("data", r)
    # with no data, the platform's reason (a complexity limit, an expired token) is the useful part
    if errors and (not partial or not isinstance(data, dict)):
        raise ValueError(f"the platform reported errors: {json.dumps(errors)[:500]}")
    if not isinstance(data, dict):
        raise TypeError("no data object in the response")
    return (data, errors) if partial else data


def console_of(data, deployment):
    """The console URL in a response's DATA, which must be DEPLOYMENT's. Raises otherwise."""
    console = (((data.get("platform") or {}).get("urls")) or {}).get("console")
    if not console:
        raise ValueError(
            "no platform.urls.console in the response: fetch it with the current query file"
        )
    try:
        name = deployment_name(console)
    except ValueError:
        raise ValueError(
            f"the response's console URL {console!r} names no deployment"
        ) from None
    if name != deployment:
        raise ValueError(
            f"it came from deployment {name!r} ({console}), not {deployment!r}: "
            "fetched through another Stacklet MCP server?"
        )
    return console


def read_account(path, deployment):
    """The account in the account.graphql response at PATH (DEPLOYMENT's), or None, said, for none.
    Raises on what it can't read."""
    data = response_data(path)
    console_of(data, deployment)
    a = data.get("account")
    if not a:
        print(
            f"warning: {path.name} holds no account (unknown key?): skipped",
            file=sys.stderr,
        )
    return a or None


def load_account(db, a):
    """Snapshot account A with the groups it is in, replacing its last. -> its PROVIDER:KEY."""
    m = a["groupMappings"]
    if m["pageInfo"]["hasNextPage"]:
        raise ValueError(
            "truncated groupMappings; raise `first:` in platform/account.graphql"
        )
    groups = sorted({e["node"]["group"]["uuid"] for e in m["edges"]})
    tags = a.get("tags") or []
    db.execute(
        "INSERT OR REPLACE INTO account"
        " (provider, key, name, path, org_id, active, groups, looked_up, tags)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            a["provider"],
            a["key"],
            a["name"],
            a["path"],
            a["orgId"],
            int(bool(a["active"])),
            json.dumps(groups),
            now_utc(),
            # null when it has none
            json.dumps(tags) if tags else None,
        ),
    )
    return f"{a['provider']}:{a['key']}"


def org_scope(dynamic_filter):
    """(org key, path prefix or None) for the filters the platform generates for its per-org and
    per-OU-path groups (an org, and at most one path); (None, None) for any other: hand-written
    filters are not interpreted."""
    r = region(dynamic_filter)
    if not r or r[0] is None or r[2] or (r[1] and len(r[1]) != 1):
        return None, None
    return r[0], r[1][0] if r[1] else None


def insert_posture(db, data):
    """The posture query's DATA -> account_group, provider_accounts, policy_collection and binding
    rows. Raises on what it can't represent faithfully."""
    posture = data["bindingPosture"]
    for key, v in data.items():  # active_<Provider>: see active_accounts.graphql
        if key.startswith("active_"):
            # NULL, not 0: the platform gives null for no accounts and for an unknown slug alike
            total = ((v or {}).get("pageInfo") or {}).get("total")
            db.execute(
                "INSERT INTO provider_accounts VALUES (?,?)",
                (key[len("active_") :], total),
            )
    for g in posture["accountGroups"]:
        db.execute(
            "INSERT INTO account_group VALUES (?,?,?,?,?,?,?,?)",
            (
                g["uuid"],
                g["name"],
                g["provider"],
                g["description"],
                g["dynamicFilter"],
                g["accountMappings"]["pageInfo"]["total"],
                *org_scope(g["dynamicFilter"]),
            ),
        )
    # optional: make_fixture's posture has none
    orgs = data.get("cloudOrganizations")
    if orgs:
        if orgs["pageInfo"]["hasNextPage"]:
            raise ValueError(
                "truncated cloudOrganizations; raise `first:` in platform/posture.graphql"
            )
        db.executemany(
            "INSERT OR IGNORE INTO cloud_org VALUES (?,?,?)",
            [
                (e["node"]["provider"], e["node"]["key"], e["node"]["name"])
                for e in orgs["edges"]
            ],
        )
    for c in posture["policyCollections"]:
        # only its size: membership comes per collection
        n = c["policyMappings"]["pageInfo"]["total"]
        db.execute(
            "INSERT INTO policy_collection VALUES (?,?,?,?,?)",
            (
                c["uuid"],
                c["name"],
                c["provider"],
                c["description"],
                n,
            ),
        )
    for b in posture["bindings"]:
        # never deployed (bindings are never undeployed): it enforces nothing yet
        if b["lastDeployed"] is None:
            continue
        db.execute(
            "INSERT INTO binding VALUES (?,?,?,?,?,?)",
            (
                b["uuid"],
                b["name"],
                b["description"],
                b["lastDeployed"],
                b["accountGroup"]["uuid"],
                b["policyCollection"]["uuid"],
            ),
        )


# Bound providers with neither an all-accounts group nor an active account count.
UNSIZED_PROVIDERS = """
SELECT DISTINCT g.provider
FROM binding b
JOIN account_group g ON g.uuid = b.account_group_uuid
WHERE g.provider NOT IN (SELECT provider FROM account_group WHERE dynamic_filter = '')
  AND g.provider NOT IN (SELECT provider FROM provider_accounts)"""

# Providers without an all-accounts group whose active account count came back null.
NULL_COUNT_PROVIDERS = """
SELECT provider
FROM provider_accounts
WHERE count IS NULL
  AND provider NOT IN (SELECT provider FROM account_group WHERE dynamic_filter = '')"""


def warn_unsized(db):
    """Warn about bound providers with no usable account total: the digest could then give no
    group's share of their accounts."""
    unsized = [r[0] for r in db.execute(UNSIZED_PROVIDERS)]
    if unsized:
        print(
            f"warning: no account total for {', '.join(sorted(unsized))} (no all-accounts group, no active "
            "count): see platform/active_accounts.graphql, and ingest again with its response after posture.json",
            file=sys.stderr,
        )
    # a mistyped slug looks exactly like an empty provider
    nulls = [r[0] for r in db.execute(NULL_COUNT_PROVIDERS)]
    if nulls:
        print(
            f"warning: the active account count for {', '.join(sorted(nulls))} is null: either no active "
            "accounts, or a provider slug the accounts filter doesn't know (see platform/active_accounts.graphql)",
            file=sys.stderr,
        )


def with_active_counts(data, paths, deployment):
    """DATA plus the active_<Provider> counts in the active_accounts.graphql responses at PATHS
    (this deployment's). Raises on a response without one: a total silently missing would skew
    every share the digest gives."""
    for path in paths:
        extra = response_data(path)
        console_of(extra, deployment)
        counts = {k: v for k, v in extra.items() if k.startswith("active_")}
        if not counts:
            raise ValueError(
                f"{Path(path).name}: no active_<Provider> count (see platform/active_accounts.graphql)"
            )
        data = {**data, **counts}
    return data


def cmd_posture(a, cache):
    """Build CACHE's posture db afresh in a temp file and swap it in; on failure, keep the old one.
    Nothing is created until the responses are known to be this deployment's, so a mistyped
    DEPLOYMENT leaves no directory for deployments.py to offer."""
    try:
        data = response_data(a.file)
        console = console_of(data, cache.deployment)
        data = with_active_counts(data, a.active, cache.deployment)
    except Exception as e:  # noqa: BLE001
        sys.exit(
            f"ingest failed, existing cache left untouched: {type(e).__name__}: {e}"
        )
    cache.make_dir()
    # unique, so refreshes can't collide
    fd, tmp = tempfile.mkstemp(dir=cache.dir, suffix=".tmp")
    os.close(fd)
    try:
        db = sqlite3.connect(tmp)
        try:
            db.executescript(SCHEMA)
            # formatted in: PRAGMA takes no bound parameters, and the value is our own int
            db.execute(f"PRAGMA user_version = {int(POSTURE_LAYOUT)}")
            insert_posture(db, data)
            warn_unsized(db)
            db.executemany(
                "INSERT INTO meta VALUES (?, ?)",
                [
                    ("refreshed_at", now_utc()),
                    ("refresh_id", uuid.uuid4().hex),
                    ("deployment", cache.deployment),
                    ("console", console),
                ],
            )
            db.commit()
        finally:
            db.close()
        os.replace(tmp, cache.posture)  # atomic swap
    except Exception as e:  # noqa: BLE001
        # leave the previous cache alone and don't leave partial tenant data behind
        try:
            os.unlink(tmp)
        except OSError:
            pass
        sys.exit(
            f"ingest failed, existing cache left untouched: {type(e).__name__}: {e}"
        )
    print(f"posture cache written to {cache.posture}")


def cmd_collections(a, cache):
    """Store the collections in the responses A.files, skipping (and naming) any it can't read. All
    are read first, so a batch from another deployment opens no cache; one transaction each, kept
    short since other sessions write this db too."""
    found, unread, loaded, skipped = [], [], [], []
    for p in a.files:
        try:
            collections, refresh, failed = read_collections(p, cache.deployment)
            found += [
                (p, alias, c, refresh, failed.get(alias))
                for alias, c in collections.items()
            ]
        except Exception as e:  # noqa: BLE001
            unread.append(f"{p.name}: {type(e).__name__}: {e}")
    if found:
        # exits if there is no posture to say what the collections are
        db = cache.open()
        names = {
            alias_of(u): n
            for u, n in db.execute("SELECT uuid, name FROM policy_collection")
        }
        for p, alias, c, refresh, failure in found:
            name = names.get(alias, alias)
            why = []
            if failure:
                why.append(f"the platform failed: {failure}")
            elif c is None:
                why.append("no such collection (deleted?)")
            else:
                try:
                    with db:
                        if load_collection(db, c, why, refresh):
                            loaded.append(name)
                except Exception as e:  # noqa: BLE001
                    why.append(f"{type(e).__name__}: {e}")
            skipped += [f"{p.name}: {name}: {w}" for w in why]
        db.close()
    print(f"loaded {len(loaded)} collection(s): {', '.join(sorted(loaded)) or '-'}")
    if unread or skipped:
        sys.exit("not loaded:\n  " + "\n  ".join(unread + skipped))


def cmd_accounts(a, cache):
    """Store the account lookups A.files, skipping (and naming) any it can't read. All are read
    first, so a batch with nothing to load creates no cache."""
    accounts, loaded, skipped = [], [], []
    for p in a.files:
        try:
            acct = read_account(p, cache.deployment)
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{p.name}: {type(e).__name__}: {e}")
            continue
        if acct:
            accounts.append((p, acct))
    if accounts:
        db = cache.open(posture=False)
        for p, acct in accounts:
            try:
                with db:
                    loaded.append(load_account(db, acct))
            except Exception as e:  # noqa: BLE001
                skipped.append(f"{p.name}: {type(e).__name__}: {e}")
        db.close()
    print(f"added {len(loaded)} account(s): {', '.join(loaded) or '-'}")
    if skipped:
        sys.exit("skipped, as unreadable:\n  " + "\n  ".join(skipped))


def main():
    ap = argparse.ArgumentParser()
    add_cache_args(ap, ("posture", "collections", "accounts"))
    sub = ap.add_subparsers(required=True)
    s = sub.add_parser(
        "posture", help="replace the posture with a posture.graphql response"
    )
    s.add_argument("file", type=Path)
    s.add_argument(
        "active",
        type=Path,
        nargs="*",
        help="active_accounts.graphql responses, for providers without an all-accounts group",
    )
    s.set_defaults(f=cmd_posture)
    s = sub.add_parser(
        "collections",
        help="add or replace collections' memberships and content (collection.graphql responses)",
    )
    s.add_argument("files", type=Path, nargs="+")
    s.set_defaults(f=cmd_collections)
    s = sub.add_parser("accounts", help="add or replace account.graphql lookups")
    s.add_argument("files", type=Path, nargs="+")
    s.set_defaults(f=cmd_accounts)
    a = ap.parse_args()
    a.f(a, Cache(a.deployment))


if __name__ == "__main__":
    main()
