# Agent skills for working with Stacklet

Coding-agent guardrails derived from a Stacklet platform's policy posture: steer coding agents
away from defining infrastructure that violates deployed policies.

## Install (Claude Code)

```
/plugin marketplace add stacklet/agent-skills
/plugin install stacklet@stacklet-agent-skills
```

Requires the Stacklet MCP server and Python 3.11+ (the scripts use only the standard library).

## Permissions

- **Stacklet**: the skill only runs GraphQL queries, through the MCP server's
  `platform_graphql_query`, and writes nothing to the platform.
- **Claude Code tools**: approve `mcp__<server>__platform_graphql_query`, `python3` running the
  skill's bundled scripts, and creating and writing files in a temp directory (the sync saves
  query results there before ingesting them). The skill doesn't pre-approve anything itself. A
  skill that declares `allowed-tools` needs an approval just to load, and headless `claude -p` runs
  skip it silently. To stop the prompts, add permission rules to your own settings. The sync runs
  as a background sub-agent, which can't answer a prompt: if it is denied a tool, the main session
  runs the sync itself and asks you there.
- **Files**: the cache is under `~/.stacklet/guardrail-cache/` (set `STACKLET_GUARDRAILS_CACHE` to
  move it). Each repo's scope is recorded in `.stacklet/guardrails/scope.json` at the repo root,
  which is meant to be committed.
- **Model calls**: distilling guardrails and assessing the posture run `claude -p` under your own
  Claude login, so they spend your tokens. To use another tool instead, see
  `plugins/stacklet/skills/guardrails/references/inference.md`.
