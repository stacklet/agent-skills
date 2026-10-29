# Guardrails skill: design

Why the skill is built the way it is, for people working on it
(`plugins/stacklet/skills/guardrails/`; paths below are relative to it). Not shipped with the
plugin. What each script does in detail is in its docstring; what the distillation is told is its
prompt (`distill.py`); what each measured change found is in its commit message.

## Layout

| file | role |
|---|---|
| `platform/*.graphql` | the queries an agent runs through the Stacklet MCP server |
| `deployments.py` | chooses the session's deployment (the only script without a DEPLOYMENT argument) |
| `ingest.py` | stores what the agent fetched: the posture (rebuilt and swapped), collections, accounts |
| `posture.py` | the posture digest the assessment reads, and what is due a fetch |
| `assess.py` | the posture assessment: which bound collections apply, and the questions settling the rest |
| `scope.py` | the repo's committed scope file (`.stacklet/guardrails/scope.json`): `decide`, `check`, `query` |
| `distill.py` | `index`, `show`, `source`: guardrails for a resource type, distilled and cached |
| `_cache.py`, `_inference.py` | one deployment's cache and how it is opened; model calls, isolated |
| `references/` | what SKILL.md's main path leaves out; `sync.md` is the sync, run by a background sub-agent |

## Data path

The scripts never talk to Stacklet: an agent fetches through the Stacklet MCP server and saves what
it gets for the scripts to ingest. Everything comes through GraphQL; dataset exports aren't used,
since their download URLs need credentials the MCP server doesn't give out. Two measured facts make
this work: Claude Code saves an MCP result over 25,000 tokens (about 50 policies) to a file, which
the agent copies, so content is never retyped; and a query naming whole collections is short to
write.

- The **posture** (`posture.graphql`, one unpaginated call) gives each collection's size, not its
  membership: that is large (one collection has had about 1,600 policies) and a repo needs little of
  it. Bindings with a null `lastDeployed` are left out: bindings are never undeployed, so these
  never were deployed.
- **Collections** are fetched lazily, membership and content together, what the repo's scope needs
  first: its in-scope collections, and those an unanswered question names, so which resource types a
  question bears on is known before anyone is asked. A small batch is filled out with other
  collections due a fetch so its result comes back as a file too (warming the cache for other
  repos).
- **Size has a ceiling:** 6 MB in one result worked, but at about 12 MB Claude Code dropped the MCP
  connection, so `sourceYAML` (the same content again) isn't fetched; the largest collection seen is
  about 3 MB. The platform doesn't cap `first:`, so `policyMappings(first: 10000)` returns a whole
  collection in one page (verified in the platform's pager); ingest still refuses a truncated one.
  Should a deployment outgrow that, `policyMappings` pages by keyset on policy name: sequential, so
  not built until needed.
- **A refresh is best effort.** A failed one leaves the old cache, so guardrails survive the MCP
  server being down; nothing refreshes a cache between sessions (that would need a direct GraphQL
  client).

## Deployments

A user may have several Stacklet deployments, each with its own MCP server, and tenant isolation is
a requirement: every script but `deployments.py` takes the deployment as its first argument, and
nothing guesses it. A deployment is named by its console URL's host, which the platform takes from
its own configuration, never from the request, so a deployment has one name however it is reached;
it means the same on every machine, unlike an MCP server's name.

`deployments.py` chooses once per session (SKILL.md step 1):

0. None while the user has turned guardrails off for the repo (recorded in the scope file): said in
   a line each session, never asked again, until they ask for guardrails.
1. The scope file's deployment when it records one, used through its server, else its cache; if
   neither is available, none is chosen, never a different one. It is read even from an otherwise
   invalid file: a broken file must not let a session drift to another tenant.
2. Else the only server; with no server, the only usable cache.
3. Else the user's answer, recorded in the scope file, so each repo is asked at most once.

Guards against mixing tenants:

- every response carries the console, and ingest refuses another deployment's;
- the posture db records its deployment, and `Cache.open` refuses one built for another;
- `decide` won't write facts against another deployment than the scope file's, and `check` and
  `distill.py` ignore such a file.

A renamed console orphans its cache, and its repos' scope files need editing by hand (no tool until
it happens).

## Two caches, deliberately

Both live in `~/.stacklet/guardrail-cache/<deployment>/`.

- **`posture.db` is derived:** rebuilt from scratch and swapped in atomically on each refresh.
- **`guardrails.db` is persistent:** never touched by a refresh, never pruned. It holds what is
  costly or slow to get again: immutable policy versions (ingest only adds; readers see the bound
  versions, while an old `name@version` still resolves), distillations, and one snapshot per
  looked-up account and per collection membership. No migrations while this is early: a db of an
  older layout (`GUARDRAILS_LAYOUT`) is deleted and started afresh, since everything in it can be
  fetched or made again; one of a newer layout is left alone and the scripts ask for a skill update,
  or two versions would delete each other's db on every open.

**Memberships and freshness.** A collection is *loaded* when its membership is fetched and every
version in it has content (or the posture says it is empty): missing data always reads as "not
loaded", never as "no guardrails". It is *checked* when it was fetched under the current posture
refresh: the platform has no change marker for a collection, and a count misses the commonest change
(a version bump), so every refresh makes every collection due again.

The query carries the refresh it was made under (a `posture_<id>` alias), so another session
refreshing between query and ingest changes nothing, and an older posture's fetch never replaces a
current one. A membership is the collection's current one, which can be ahead of what a binding that
doesn't auto-deploy has deployed. Disabled policies are deliberately not a concept: a membership is
what the platform returns.

## Distillation

`show` distils each loaded in-scope collection's policies of one resource type separately, and
prints their sections. Per collection, not per type: collections load lazily, so a per-type
distillation would be invalidated by every new one, and could carry an out-of-scope framework into
in-scope bullets. A policy in several collections repeats in each (no de-duplication). Always
scoped: an unscoped view would present collections nobody asked about as if they applied.

- **Key:** collection, resource type, and a hash of the exact uuid+version set of that collection's
  policies of that type. Any distillation of the same policies is good enough, whatever prompt or
  model made it; `show --redistill` redoes one. Each is written in its own short transaction, never
  held across a model call.
- **Bundles group policies by what they select** (filters, `query`, `conditions`), so the model sees
  each condition once; actions, severity, compliance and mode stay per policy.
- **What the model never sees:** a policy's description (often stale or looser than its filters, and
  the evals lost nothing without it), what an action's message says or where it goes
  (`MESSAGE_FIELDS`), and a policy's `schedule` (Stacklet ignores it: a binding's executions decide
  when a policy runs). None of these says what a resource must be, and free text written for people
  is where stray instructions would be.
- **What a bullet says** is set by the prompt, each rule adopted only on a clear measured gain (its
  commit says by how much). The distillation's evals (`tests/evals/distill/`) keep measuring them.
  Faithfulness beyond that is not checked: validation (below) checks that bullets cite real
  policies, not that their prose is right, and a second model checking the first was judged not
  worth it while the evals show few misses.
- **Validation:** a `name@version` ref must name one of the bundle's policies (one at a version the
  bundle lacks is made up, and removed); only each bullet's first line is kept, and only if it cites
  a policy of the type; the only heading kept is the runtime-only one. Output with no bullet left is
  asked for once more, then rejected, never cached.

## Model calls

Distillation (haiku) and the posture assessment (sonnet) run `claude -p` isolated from the repo:
from an empty temp dir, with no tools, no MCP servers and user settings only. Otherwise the nested
call would load the repo's CLAUDE.md (verified: a planted instruction was obeyed), and what it
writes is cached for every repo on the deployment. `$STACKLET_GUARDRAILS_INFERENCE_CMD` replaces the
command (it must isolate itself too), and `show` marks what a custom command made.

## The skill's shape

SKILL.md is written around one goal: before the agent writes code defining a resource type, the
guardrails for that type in the repo's scope are in its context (`distill.py show`), from a cache
complete for that scope and current. It was first a procedure (deployment, sync, scope, consult)
whose every condition was a step everyone walked through, and the steps tangled. Now the main path
is short, and the scripts say what keeps the goal from being met: every note `index` and `show`
print ends in a tag (`[sync: refresh]`, `[sync: fetch]`, `[question]`, `[tell the user]`) that
SKILL.md maps to its handling, with details in `references/`.

- The agent waits only when guardrails may be *missing* for the type it is about to write
  (collections not loaded, accounts not looked up, no assessment). Old data is used while the sync
  checks it, and `show` runs again when the sync reports.
- Which questions bear on a type is the scripts' to say: `show` names those whose collections have
  guardrails for it.
- The sync is mechanical, so it runs as a background sub-agent where one can start, ending with a
  fixed-shape report. Only it makes the assessment, so nothing makes a second beside it. A sub-agent
  can't answer permission prompts: one denied a tool says so, and the main agent syncs itself.
- A new posture with changed bindings has no assessment until a model call ends (minutes); the
  newest earlier one stands in, its questions held back, since its judgements of the collections
  still bound beat the all-accounts defaults.

## Scope model

A repo's scope file records **facts**, each with its reason: answers to the assessment's questions,
the accounts the code deploys to, and include/exclude decisions on collections. Scope is derived
from them against the live posture on every read, so nothing derived goes stale. Everything is
identified by uuid, question id or `PROVIDER:KEY`, with names alongside for people.

**Scope is a union**, because the same code often deploys into many accounts and the goal is every
guardrail that applies anywhere it lands; nothing is ruled out by omission, and we never ask for a
complete list of accounts:

    in scope = assessment's applies + what yes-answers add - what they remove
               + what looked-up accounts reach + includes - excludes

**The posture assessment** (`assess.py`) replaces counting rules (coverage thresholds, group sizes):
postures in the wild roll a collection out over a dozen tag-batch groups, each small but together
everything minus an opt-out tag, so what a binding means depends on reading groups together, which a
model does well and rules don't.

- Rules decide the certain cases: bound to an all-accounts group, it applies; no policies, ignored.
- A model reads the posture digest (no policy content) and judges every other bound collection:
  `applies`, `ignore` (reached only through specific accounts, so only a lookup brings it in), or
  `ask` (a part of the estate a developer can know their code by: an org, an OU, an environment
  tag), with the fewest yes/no questions settling the `ask` ones. A question names groups, and what
  a yes changes is derived from the bindings, never the model's list.
- It depends on the posture alone, so it is made once per set of bindings and shared by every repo
  on the deployment, by the isolated model call, never the session agent: repo content must not
  steer a judgement every repo reuses. It is validated before caching; a well-formed but wrong
  judgement can't be caught that way.
- With no assessment at all, only all-accounts collections apply, and the scripts say so.
- The digest's shares use a group holding all of a provider's active accounts, never
  `accountSummary`, which counts inactive ones too.

**Account facts.** A looked-up account brings in every collection bound to a group it is in, even
one the assessment ignores or an answer removes; account discovery keeps a group per OU path, so
hierarchy is just group membership. An account not looked up adds nothing and is reported; a
snapshot over a day old still counts until a sync refreshes it. Which accounts are in play is the
agent's judgement about the change (a local profile or state file may be another project's), so
the scripts read neither.

**Answers** count only while the assessment asks the same question in the same words. An include
says only that a collection applies, not where the code deploys, so nothing is inferred from it.

**Excludes always win**, and they and what a yes-answer removes are reported on every `check`,
`index` and `show`. Nothing stops or reviews them: the scope file is committed, so it is visible and
reviewable in the repo, and the team's judgement is trusted (guardrails are facts, not orders).
Group priority and the `system` flag are deliberately ignored.

## Known gaps

- Nothing has run end to end on a live deployment with acting policies: policies' actions, `query`
  and `conditions` as found in the wild are in the distillation's evals (24-38), and the assessment
  was checked offline on 20 postures from the wild (8 such postures, names invented, are its evals),
  but the skill's evals see actions only in fixtures.
- Policies' actions reach agents as names (`notify`, `tag`, `delete`, a `mark-for-op`'s deferred
  op), not classified: agents read those better than a destructive/not flag.
- A cached distillation or assessment is model-written text served to every repo on the
  deployment; isolation and validation limit what it can carry, not whether it is right.
- What the agent does when code knowingly keeps a violation (a comment in the code saying why, and
  not raising it again) is undecided.
- Windows is untested: `python3` may not exist (SKILL.md offers `python` or `py -3`); encoding is
  handled, paths aren't checked.
- Triggering is unmeasured in a session crowded with skills. In isolated runs the skill was the
  first tool call on 36/36 should-trigger queries, and a CLAUDE.md snippet only added false
  positives, so none is shipped.
- What the evals don't show is in `tests/evals/README.md`.
