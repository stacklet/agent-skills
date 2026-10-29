# Choosing the deployment: the cases SKILL.md leaves here

A user may have several Stacklet deployments, each with its own MCP server. `deployments.py` chooses
one: the repo's scope file when it names one, else the only server (or, with none, the only cached
deployment). These are the cases where its answer needs more than using it.

- **The scope file chose, and `servers` or `cached` include another deployment:** tell the user in a
  line which deployment the scope file chose. A committed file is choosing the tenant.
- **`use` null with `ask` non-empty:** ask the user which Stacklet deployment governs this code,
  offering each console URL in `ask`. Use the one they choose. Once its posture is cached (already,
  if they chose a cached deployment; else when a sync reports), record their answer so it isn't
  asked again: `scope.py <deployment> decide --because "<what the user said>"`. If they choose none,
  they have turned guardrails off: record it, `deployments.py --disable --because "<what the user
  said>"`, so later sessions say so in a line instead of asking again. If the sync fails, record
  nothing: guardrails are unavailable this session, and the question comes back next time.
- **`use` null otherwise** (the user turning guardrails off included): guardrails are unavailable
  this session; say why (`reason`) in a line, and carry on without them.
- **The user turns guardrails off, or asks for them again,** at any point: `deployments.py
  --disable --because "<what the user said>"`, or `deployments.py --enable [--console ..]`, which
  lifts it and chooses again (SKILL.md step 1).
- **`reason` says "no refresh":** there is no MCP server for that deployment, so nothing can be
  synced: use its cache as it is (SKILL.md, "Syncing", says what to tell the user).

## A server appearing later

If there was no server for the deployment (or guardrails were unavailable) and a Stacklet MCP server
becomes available later in the session (the user authenticates it, say), choose again (SKILL.md step
1.1). If it chooses the same deployment, sync it in `refresh` mode; if another, ask the user before
switching. Then `show` again each resource type you have already written code for, and tell the user
about any guardrail that would change it.

## A scope file for another deployment

`scope.py check` reports `deployment_mismatch`, and `index`/`show` warn that the file is scoped
against another deployment: it was ignored (defaults only). The file may have changed under you (a
branch switch, say): choose again, and use the deployment it chooses. Don't edit the file yourself.
