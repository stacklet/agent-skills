"""A stand-in Stacklet MCP server for the skill's flow evals: serves a generated fixture over stdio.

usage: fake_stacklet_mcp.py FIXTURE_DIR LOG [--pad KB] [--delay SECONDS] [--fail COLLECTION_UUID ..]
(--fail last: it takes the rest)

FIXTURE_DIR is make_fixture.py's output (posture.json, collections.json, account_*.json). It offers
the real server's GraphQL tools, as the real server describes them (an agent reads those words, and
they tempt it into extra calls), with the real server's instructions. `platform_graphql_query`
answers the queries the skill sends, told apart by their shape: deployment.graphql (the console),
posture.graphql, account.graphql (by its variables, an object or a JSON string as the real tool
takes them), and collection.graphql as scope.py/posture.py print it (each aliased
`policyCollection` line from collections.json, echoing the `posture_<id>` alias). A collection
named by --fail comes back null with an error on its alias, as the platform reports one it fails to
serve. Anything else is an error: the skill sending a query the evals don't know is worth seeing.
The other tools answer briefly. A tool call that raises is answered as an MCP error, not a crash.

--pad adds KB of padding to every answer (in the response's `extensions`, which nothing reads), so
results pass the 25,000 tokens over which Claude Code saves an MCP result to a file: what the skill
copies on a real deployment, where a sync's results are that large. --delay waits before each
answer, as a real platform takes seconds per batch.

Every call is appended to LOG as a JSON line: {"tool", "kind", "exact", "collections", "account",
"errors"}: the tool, what the query asked for, whether it was the skill's own query (whitespace
aside), the collections or account it named, and how many errors it got. The flow evals grade it:
what was fetched, how often and in what order. Results are wrapped as the real server wraps them
({"query", "variables", "data", "errors"}). Standard library only, newline-delimited JSON-RPC, as
Claude Code speaks MCP over stdio.
"""

import json
import re
import sys
import time

from pathlib import Path

from make_fixture import (
    alias_of,  # beside it: the skill's aliases, as the fixture makes them
)


PROTOCOL = "2025-06-18"
PLATFORM = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "stacklet"
    / "skills"
    / "guardrails"
    / "platform"
)
# The real server's, as Claude Code shows them (trimmed of what names other toolsets).
INSTRUCTIONS = """Stacklet provides comprehensive cloud governance and analytics capabilities through
integrated toolsets.

## Start Here: Critical Info Tools
Always call these info tools first - they contain essential guidance and best practices:
- "platform_graphql_info" - For governance operations

### Cloud Governance Operations
Execute governance policies and manage compliance at scale:
- Deploy and manage Cloud Custodian policies across accounts and regions
- Monitor policy execution results and resource compliance
- Configure account grouping and binding relationships"""
TOOLS = [
    {
        "name": "platform_graphql_query",
        "description": (
            "Execute GraphQL queries against the Stacklet Platform for governance operations.\n\n"
            "This is your main tool for querying policies, account groups, bindings, resources,\n"
            "executions, and all other governance data. Supports both queries and mutations.\n\n"
            "⚠️  Important guidelines:\n"
            '- Always query for "problems" field alongside your data\n'
            "- Use small page sizes (5-10) for exploration, larger for known datasets\n"
            "- Check types with platform_graphql_get_types() first\n"
            "- For large exports, use platform_dataset_export() instead"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "GraphQL query string to execute against the Stacklet Platform API",
                },
                "variables": {
                    "anyOf": [{"type": "object"}, {"type": "string"}, {"type": "null"}],
                    "description": "Variables to pass to the GraphQL query",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "platform_graphql_info",
        "description": (
            "Essential guide for Stacklet Platform GraphQL API - read this first before using "
            "other tools."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "platform_graphql_get_types",
        "description": "Get detailed GraphQL type definitions to understand schema structure.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type_names": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["type_names"],
        },
    },
]
COLLECTION_LINE = re.compile(
    r'^(\w+)\s*:\s*policyCollection\(\s*uuid:\s*"([^"]+)"\s*\)\s*\{\s*\.\.\.Collection\s*\}$'
)
POSTURE_LINE = re.compile(r"^(posture_\w+)\s*:\s*__typename$")


def lines_of(query):
    """QUERY's lines, comments dropped and whitespace normalised: what makes two queries the same."""
    out = []
    for line in query.splitlines():
        line = " ".join(line.split("#", 1)[0].split())
        if line:
            out.append(line)
    return out


def is_skills(query, name):
    """Whether QUERY is platform/NAME, whitespace and comments aside (a collection query: with any
    number of correctly aliased collection lines, and its posture line)."""
    want = [
        x
        for x in lines_of((PLATFORM / name).read_text(encoding="utf-8"))
        if "__UUID__" not in x and "__POSTURE__" not in x
    ]
    got = []
    for x in lines_of(query):
        m = COLLECTION_LINE.match(x)
        if m:
            if m.group(1) != alias_of(m.group(2)):
                return False
        elif not POSTURE_LINE.match(x):
            got.append(x)
    return got == want


class Fixture:
    def __init__(self, d, fail):
        def data(name):
            r = json.loads((d / name).read_text(encoding="utf-8"))
            return r.get("data", r)

        self.posture = data("posture.json")
        self.console = self.posture["platform"]
        self.collections = {
            v["uuid"]: v
            for k, v in data("collections.json").items()
            if isinstance(v, dict) and "uuid" in v
        }
        self.accounts = {}
        for p in sorted(d.glob("account_*.json")):
            a = data(p.name).get("account")
            if a:
                self.accounts[(a["provider"], a["key"])] = a
        self.fail = set(fail)

    def answer(self, query, variables):
        """-> (data, errors, what to log)."""
        if "bindingPosture" in query:
            return (
                self.posture,
                [],
                {"kind": "posture", "exact": is_skills(query, "posture.graphql")},
            )
        named = re.findall(r'(\w+)\s*:\s*policyCollection\(\s*uuid:\s*"([^"]+)"', query)
        if named:
            out, errors = {"platform": self.console}, []
            alias = re.search(r"\b(posture_\w+)\s*:\s*__typename", query)
            if alias:
                out[alias.group(1)] = "Query"
            for name, u in named:
                if u in self.fail:
                    out[name] = None
                    errors.append(
                        {"message": "internal error", "path": [name, "policyMappings"]}
                    )
                else:
                    out[name] = self.collections.get(u)
            return (
                out,
                errors,
                {
                    "kind": "collections",
                    "exact": is_skills(query, "collection.graphql"),
                    "collections": [u for _, u in named],
                },
            )
        if re.search(r"\baccount\s*\(", query):
            key = (variables.get("provider"), variables.get("key"))
            return (
                {"platform": self.console, "account": self.accounts.get(key)},
                [],
                {
                    "kind": "account",
                    "exact": is_skills(query, "account.graphql"),
                    "account": ":".join(map(str, key)),
                },
            )
        if "urls" in query and "console" in query:
            return (
                {"platform": self.console},
                [],
                {"kind": "deployment", "exact": is_skills(query, "deployment.graphql")},
            )
        return (
            None,
            [{"message": "fake server: query not understood"}],
            {"kind": "other", "exact": False},
        )


def call(fixture, name, args, pad):
    """Tool NAME's answer to ARGS -> (its text, what to log)."""
    if name == "platform_graphql_query":
        query, variables = args.get("query") or "", args.get("variables") or {}
        if isinstance(variables, str):  # the real tool takes JSON text too
            variables = json.loads(variables) if variables.strip() else {}
        data, errors, logged = fixture.answer(query, variables)
        body = {"query": query, "variables": variables, "data": data}
        if errors:
            body["errors"] = errors
        if pad:
            body["extensions"] = {"padding": "x" * (pad * 1024)}
        return json.dumps(body), {**logged, "errors": len(errors)}
    if name == "platform_graphql_info":
        return (
            "The Platform API is GraphQL, with connection-based pagination "
            "(first/after, pageInfo { total hasNextPage })."
        ), {"kind": "info"}
    if name == "platform_graphql_get_types":
        return "type Query { platform: Platform }", {"kind": "types"}
    raise ValueError(f"no tool {name}")


def option(argv, flag, default):
    return type(default)(argv[argv.index(flag) + 1]) if flag in argv else default


def main():
    argv = sys.argv
    d, log = Path(argv[1]), Path(argv[2])
    fail = argv[argv.index("--fail") + 1 :] if "--fail" in argv else []
    pad, delay = option(argv, "--pad", 0), option(argv, "--delay", 0.0)
    fixture = Fixture(d, fail)
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:  # a notification: nothing to answer
            continue
        method, params = msg.get("method"), msg.get("params") or {}
        error = None
        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion", PROTOCOL),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "stacklet", "version": "0"},
                "instructions": INSTRUCTIONS,
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            name = params.get("name")
            try:
                text, logged = call(fixture, name, params.get("arguments") or {}, pad)
                result = {"content": [{"type": "text", "text": text}]}
            except Exception as e:  # noqa: BLE001
                # as a tool error, as the real server reports one: the session carries on
                logged = {"kind": "failed", "error": f"{type(e).__name__}: {e}"}
                result = {
                    "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                    "isError": True,
                }
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"tool": name, **logged}) + "\n")
            time.sleep(delay)
        elif method == "ping":
            result = {}
        else:
            error = {"code": -32601, "message": f"no method {method}"}
        reply = {"jsonrpc": "2.0", "id": msg["id"]}
        reply.update({"error": error} if error else {"result": result})
        print(json.dumps(reply), flush=True)


if __name__ == "__main__":
    main()
