# Agent skills for working with Stacklet

Coding-agent guardrails derived from a Stacklet platform's policy posture: steer coding agents
away from defining infrastructure that violates deployed policies.

## Install (Claude Code)

```
/plugin marketplace add stacklet/agent-skills
/plugin install stacklet@stacklet-agent-skills
```

Requires the Stacklet MCP server and Python 3.11+ (the scripts use only the standard library).

The skill runs its bundled scripts with `python3`, so Claude Code asks you to approve the first
call. (Don't expect the skill to pre-approve this itself: a skill that declares `allowed-tools`
needs an approval just to load, and is silently skipped in headless `claude -p` runs.)

To stop the prompts, add a permission rule for the skill's scripts in your own settings.
