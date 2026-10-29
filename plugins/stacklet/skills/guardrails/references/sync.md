# Syncing the guardrail cache

Fetch what a repo's guardrails need from one Stacklet deployment, through its MCP server, into the
local cache. Usually run by a background sub-agent while the main agent gets on with the task, and
otherwise by the main agent itself. It is mechanical: no judgement about the code, the scope or
the user is needed, and none should be made.

If you are the main agent (no sub-agent could, or one was denied tools): follow the steps, skipping
the posture if a sub-agent already refreshed it, then act on what the report would say as SKILL.md
says, rather than ending with the report.

You are given:

- `<skill>`: the guardrails skill's directory (absolute path).
- `<deployment>`: the deployment, as `deployments.py` named it.
- `<server>`: the Stacklet MCP server to fetch through (its tools are
  `mcp__<server>__platform_graphql_query` and so on). Use no other server: `ingest.py` refuses
  another deployment's data.
- `<repo>`: the repo the guardrails are for (an absolute path). Pass it to every `scope.py` command
  as `--repo <repo>`, wherever you are running: the repo's scope file is read from there.
- `<mode>`: `refresh` (fetch the posture first) or `fetch` (only what is missing or due a check).

## Rules

- Only fetch and ingest. Don't edit code or the scope file (never run `scope.py decide`), don't
  answer the assessment's questions, and don't ask the user anything: report instead.
- Run each script as its own plain command, `python3 <skill>/scripts/<script>.py <deployment> ...`
  (`python` or `py -3` on Windows if `python3` isn't there), with no pipes or `&&` chains.
- Create one fresh temp directory (for example with `mktemp -d`) and use its literal absolute path
  as `<dir>` below: shell variables don't survive between separate tool calls.
- When an MCP tool saves a large result to a file and gives you its path, copy that file to where
  it is wanted rather than retyping it. A result that comes back inline instead (a small one): write
  it to the file exactly as returned, changing nothing.
- Keep every stderr line starting `note:` or `warning:` for the report.
- A step that fails (the MCP server is unavailable, a fetch errors, an ingest exits non-zero): note
  why and carry on with the next. What ingest couldn't read it names, and leaves as it was; the
  rest of its files still load.
- If a tool or command is denied you (you can't answer a permission prompt), stop and report it
  under `tools denied`: the main agent then syncs itself.

## Steps

1. **Posture** (`refresh` only). `platform_graphql_query` with the query in
   `<skill>/platform/posture.graphql`; save the result as `<dir>/posture.json`, then
   `ingest.py <deployment> posture <dir>/posture.json`. If it warns that a provider has no account
   total, follow `<skill>/platform/active_accounts.graphql` for that provider and ingest again with
   its response after the posture (`ingest.py <deployment> posture <dir>/posture.json
   <dir>/active_<n>.json`). If the posture can't be fetched or ingested, carry on with the cached
   one: everything below still works on it. If there is no cached posture either, stop and report.
2. **Scope.** `assess.py <deployment> show`, with a command timeout of 10 minutes: it makes the
   posture assessment if the bindings changed since the last (a model call; if it fails, report why
   and carry on: the last one, if any, stands in).
3. **Accounts**: `scope.py <deployment> check --repo <repo>` (after step 2, since the main agent may
   have recorded accounts meanwhile). It exits 1 when it has a question for the user, or the scope
   file can't be used: that is normal here, neither is yours to settle (a failure prints no JSON
   report, only an error). Read the report as printed, without piping it anywhere. For each account
   its `accounts` gives as `stale` or `not looked up`:
   `platform_graphql_query` with the query in `<skill>/platform/account.graphql` and variables
   `{"provider": "<provider>", "key": "<key>"}`, saved as `<dir>/account_<n>.json`; then
   `ingest.py <deployment> accounts <dir>/account_*.json`. A null `account` means the platform
   doesn't know that key: report it (the main agent tells the user and removes it from the scope
   file). Accounts come before collections because they can bring collections into scope.
4. **Collections**: the membership (which policy versions they bind) and content of the collections
   the repo's scope needs, in one fetch. `scope.py <deployment> query --repo <repo>` prints the
   queries for those due a fetch (nothing, and "nothing to fetch", if none is), each headed by a
   `# query` comment: the in-scope collections, as the accounts just looked up place them, and those
   an unanswered question names (so the main agent can tell which questions matter). It may add
   other collections to a small batch: that is so its result comes back as a file. Run every query
   with `platform_graphql_query`, all of them in one message (they are independent), save each
   result as `<dir>/collections_<n>.json`, then load them all with `ingest.py <deployment>
   collections <dir>/collections_*.json`. A collection the platform reports as empty is normal; one
   it doesn't know (null), or failed to serve, is named by ingest, and the rest of its batch still
   loads. Fetch each collection once per sync: every collection ingest names as not loaded goes in
   the report (`not loaded`), not into another fetch.
5. **Check.** `scope.py <deployment> check --repo <repo>`; any in-scope collection still
   `"loaded": false` or `"checked": false` goes in the report too, with why if you know.

## Report

End with this, and nothing after it:

```
SYNC REPORT
deployment: <deployment>
posture: refreshed | cached, not refreshed (fetch mode) | refresh failed: <why>
assessment: <the `assessment` field of the last check; and if `assess.py show` failed, why>
in scope: <n> collection(s), <n> loaded and checked
not loaded: <name: why>, ... | none
not checked: <name: why>, ... | none
accounts looked up: <PROVIDER:KEY>, ... | none
accounts the platform doesn't know: <PROVIDER:KEY>, ... | none
tools denied: <tool or command>, ... | none
for the user: <the substance of each note:/warning: line, and each failure, once> | nothing
```

A collection not loaded has its guardrails *missing*, not absent: say so plainly, never as "no
guardrails".
