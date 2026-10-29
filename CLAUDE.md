# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

Coding-agent guardrails derived from a Stacklet platform's policy posture: steer coding agents
away from defining infrastructure that violates the policies deployed by that platform. The
product thinking is in "Agent Skills User Stories.md" (not in this repo; ask the user for it).
Its key points:

- Guardrails are **policies**; deployment scopes are **accounts**. A set of accounts is an
  **account group**, a set of policies a **policy collection**; one of each linked is a
  **binding**. The totality of bindings is the platform's **posture**.
- Implementation details in that doc are evidence of feasibility, not requirements.
- Perfection is not the goal: a skill that merely halves runtime violations is valuable.
- Never dogmatic enforcement: users may have good reasons to violate a guardrail. Present
  guardrails as *facts*, not orders.

Packaged as a Claude Code plugin marketplace: `.claude-plugin/marketplace.json` ->
`plugins/stacklet` (skill at `plugins/stacklet/skills/guardrails`). The plugin has no
`plugin.json`, deliberately: Claude Code ignores a marketplace entry's `mcpServers` when the plugin
ships one, and each deployment serves its own marketplace entry, which adds its MCP server. Still
prototype-quality code. `docs/design.md` says why it is built as it is; read it first.

## Data comes from the Stacklet MCP server

The scripts never talk to Stacklet: an agent runs the queries in
`plugins/stacklet/skills/guardrails/platform/` through the Stacklet MCP server's tools and saves the
results for `ingest.py`. Use `platform_graphql_get_types` when unsure of the API. Dataset exports
aren't used (their download URLs need credentials the MCP server doesn't give out). The flow and
its measured limits are in docs/design.md, "Data path".

## Architecture decisions (settled with the user; don't re-litigate)

Each is argued in docs/design.md, under the section named.

- **Two caches** ("Two caches, deliberately"): `posture.db` is derived and rebuilt on each refresh;
  `guardrails.db` is persistent (policy versions, distillations, account and membership
  snapshots), never pruned, and started afresh rather than migrated when its layout changes.
- **A membership is checked only under the posture refresh it was fetched under**: the platform has
  no change marker for a collection.
- **Deployments are named by their console URL's host**, each with its own cache directory, and
  every script but `deployments.py` is told which ("Deployments"): tenant isolation is a
  requirement.
- **Distillation is per (collection, resource type)**, keyed by the exact policy-version set, so
  prompt or model changes never invalidate it; bundles group policies by what they select, and the
  model never sees a description, an action's message or a `schedule` ("Distillation").
- **Scope = a posture assessment plus recorded facts**, not counting rules: a model, isolated and
  shared by every repo on the deployment, judges the bindings once; the committed scope file holds
  facts with reasons; scope is a union, and excludes always win and are always surfaced ("Scope
  model"). The `system` flag and group `priority` are deliberately ignored.
- **Policy names are unique** (`<RepositoryView.namespace>:<name>`, enforced in-platform), so
  `name@version` refs are trusted.
- Policy content is `source`, the policy as JSON (builtin `json`); models and agents read it as
  JSON too. The scripts are stdlib-only (no PyYAML: the scope file is JSON).
- SKILL.md is organised around a goal (guardrails for a type in context before code for it is
  written), with the scripts' notes ending in tags it maps to handling ("The skill's shape").
- Accounts are found by the agent: which are in play for a change is a judgement, so the scripts
  read neither the code nor the local environment.
- Server-side distillation is rejected for now (don't weld it onto the platform data model).

## Platform quirks learned the hard way

- A group with `dynamicFilter == ""` matches all *active* accounts in its provider (its
  `member_count` is exact). `accountSummary` counts inactive accounts too, so it is never a
  provider's total; `accounts(...)` is active-only unless filtered on `active`. Some providers lack
  such a group: see `platform/active_accounts.graphql`.
- Bindings are never undeployed, so a null `lastDeployed` means never deployed: ingest leaves them
  out.
- Nested `pageInfo { total }` (e.g. `accountMappings(first: 1) { pageInfo { total } }`) works and
  is cheap. A connection's `first` defaults to 10 and isn't capped.
- Model calls (`scripts/_inference.py`) run `claude -p` from an empty temp dir with user settings
  only: otherwise the nested call loads the *repo's* CLAUDE.md (verified: a planted instruction was
  obeyed), and its output is cached for every repo. The binary is `$CLAUDE_CODE_EXECPATH` when set,
  else `claude`, run as given with no `which` (on Windows that would search the repo first). Tests
  unset `$CLAUDE_CODE_EXECPATH` so they only reach their stub. Overriding the command or model:
  `_inference.py`'s docstring and `references/inference.md`.

## Known gaps

In docs/design.md ("Known gaps"); what the evals don't show is in tests/evals/README.md. What a
session most needs: a cache is only as fresh as the last session that synced it, and the skill's
evals see policy actions only in fixtures (real shapes are in the distillation's evals).

## Working conventions

- Run scripts with plain `python3` (stdlib only, Python 3.11+, also under a C locale; keep
  it that way: no third-party deps, explicit `encoding="utf-8"`, no syntax newer than 3.11).
- Tests: `just test` (pytest via uv, args passed through; no model calls). Lint: `just lint`
  (pre-commit via uv). Evals cost money, so only when asked (tests/evals/README.md):
  `just evals OUT --yes` then `tests/evals/grade_skill.py OUT/iter`; `just assess-evals OUT --yes`
  then `tests/evals/grade_assess.py OUT`; `just distill-evals OUT --yes` then
  `tests/evals/grade_distill.py OUT`. What each prompt change measured is in its commit's message.
  Postures and policies from the wild stay outside the repo; only ones with every name invented go
  in.
- Don't commit unless asked. Don't delete the user's files without asking.
- Keep new script code in the style of the prototype: small scripts, stdlib + sqlite, comments that
  explain *why*.
