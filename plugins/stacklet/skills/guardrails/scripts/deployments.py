"""Which Stacklet deployment this session should use (SKILL.md step 1).

usage: deployments.py [--console URL ..] [--enable]
       deployments.py --disable --because TEXT

--console: the console URL each Stacklet MCP server answered with (deployment.graphql); leave out
servers whose query failed. Prints JSON: `use`, the deployment to pass to every other script (null
when there is none), and `reason`; or, when only the user can say, `ask`: the deployments to offer
them. In order:

  0. The user turned guardrails off for this repo (`--disable`, recorded in its scope file): none,
     until they ask for guardrails again (`--enable` lifts it, then chooses as below).
  1. The repo's scope file records a deployment: use it, always (its server if one answers, else its
     cache). If it has neither, there is nothing to use: never another deployment instead.
  2. One server answers: use it.
  3. No server: the only cached deployment, if there is exactly one.
  4. Otherwise ask the user, and record the answer in the scope file (`scope.py DEPLOYMENT decide
     --because ...`) so the question isn't asked again.

Also lists the cached deployments. Never creates a cache; writes only the scope file, and only for
--disable or --enable.
"""

import argparse
import json
import sqlite3
import sys

from _cache import CACHE_DIR, POSTURE_LAYOUT, deployment_name
from scope import ScopeFile


def describe(d):
    """D's posture meta ({"deployment", "console", "refreshed_at", ..}), or None when D has no
    posture db of this layout."""
    path = d / "posture.db"
    if not path.is_file():  # don't let connect create one
        return None
    try:
        db = sqlite3.connect(str(path))
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] != POSTURE_LAYOUT:
                return None
            return dict(db.execute("SELECT key, value FROM meta"))
        finally:
            db.close()
    except sqlite3.Error:
        return None


def cached():
    """{name: {"console", "refreshed_at"}} for each deployment whose posture cache the scripts can
    read (this layout, built for it): choosing another would leave a cache it can't use and nothing
    to refresh it from."""
    out = {}
    if not CACHE_DIR.is_dir():
        return out
    for d in sorted(CACHE_DIR.iterdir()):
        meta = describe(d) if d.is_dir() else None
        if meta and meta.get("deployment") == d.name:
            out[d.name] = {
                "console": meta.get("console"),
                "refreshed_at": meta.get("refreshed_at"),
            }
    return out


def servers(consoles):
    """{name: console URL} for the servers' CONSOLES; one naming no deployment is said and left
    out."""
    out = {}
    for c in consoles:
        try:
            out[deployment_name(c)] = c
        except ValueError as e:
            print(f"warning: ignoring --console {c!r}: {e}", file=sys.stderr)
    return out


def choose(answering, cache, recorded, disabled=None):
    """-> (use, reason, ask: {name: console} to offer the user) by the rules in the module
    docstring, from the servers ANSWERING, the CACHE, the deployment the scope file RECORDED and why
    the user DISABLED guardrails, if they did."""
    if disabled:
        return (
            None,
            (
                f"the user turned guardrails off for this repo ({disabled}): say so in a line, and "
                "that asking for them (/stacklet:guardrails) turns them back on"
            ),
            {},
        )
    if recorded:
        if recorded in answering:
            return recorded, "the repo's scope file records it", {}
        if recorded in cache:
            return (
                recorded,
                (
                    "the repo's scope file records it; no MCP server answers for it, so the "
                    "cache only (no refresh)"
                ),
                {},
            )
        return (
            None,
            (
                f"the repo's scope file records {recorded!r}, which no MCP server answers for "
                "and nothing has cached: guardrails are unavailable (tell the user)"
            ),
            {},
        )
    if len(answering) == 1:
        return next(iter(answering)), "the only Stacklet MCP server", {}
    if not answering:
        if len(cache) == 1:
            return (
                next(iter(cache)),
                "no MCP server; the only cached deployment (no refresh)",
                {},
            )
        if not cache:
            return (
                None,
                "no MCP server and nothing cached: guardrails are unavailable",
                {},
            )
        offer = {n: c["console"] for n, c in cache.items()}
        return None, "no MCP server and several cached: ask the user", offer
    return None, "several Stacklet MCP servers: ask the user", answering


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--console",
        # extend: an agent may well repeat the flag per server, and a second one must not replace
        # the first (one server left would decide alone, where the user should have been asked)
        action="extend",
        nargs="+",
        default=[],
        metavar="URL",
        help="the console URL each Stacklet MCP server answered with",
    )
    switch = ap.add_mutually_exclusive_group()
    switch.add_argument(
        "--disable",
        action="store_true",
        help="record that the user turned guardrails off for this repo (with --because)",
    )
    switch.add_argument(
        "--enable",
        action="store_true",
        help="the user asked for guardrails: lift a --disable, then choose",
    )
    ap.add_argument("--because", help="what the user said, for --disable")
    a = ap.parse_args()
    if a.disable and not a.because:
        ap.error("--disable needs --because")
    scope_file = ScopeFile()
    if a.disable or a.enable:
        scope_file.set_disabled(a.because if a.disable else None)
    answering, cache = servers(a.console), cached()
    recorded = scope_file.deployment
    use, reason, ask = choose(answering, cache, recorded, scope_file.disabled)
    report = {
        "use": use,
        "reason": reason,
        "ask": [{"deployment": n, "console": c} for n, c in sorted(ask.items())],
        "servers": answering,
        "cached": cache,
        "scope_file": scope_file.path.as_posix() if scope_file.path.exists() else None,
        "scoped_against": recorded,
        "disabled": scope_file.disabled,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
