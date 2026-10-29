# Evals

Three sets, graded separately, each costing money to run, so their runners only print the plan
unless given `--yes`:
- the skill's (`skill/`): agent sessions, with the skill against without it;
- the posture assessment's (`assess/`): model calls judging postures from the wild;
- the distillation's (`distill/`): model calls summarising policies.

None runs in `just test`, but their own tests do, with no model calls: the skill evals' controls
(each eval's `test_checks.py`, and `skill/test_grading.py`), and the other two sets' runners,
graders and controls (`tests/test_run_assess.py`, `tests/test_run_distill.py`). The fixtures are
shared with the unit tests, in `tests/fixture/`. Grading is regex-based: read failures before
trusting them, since many have been the grader's.

## What these evals don't show

With the skill, nearly every skill eval run is clean, which is also the problem: the grader is near
its ceiling, so it can't tell a better skill text from a worse one, only count choices its checks
accept either way. Without the skill no discriminating eval had a clean run, while the guards mostly
passed (2026-10-16, 7 runs per arm). The weaknesses, as of 2026-10-28:

- **Fixtures, not estates.** The first fixture is a toy (about 25 policies, one acting, one org);
  the estate fixture is shaped like a large estate (waves, opt-outs, two GCP orgs) but its policies
  are few and invented. Policy shapes from the wild reach only the distillation's evals; hundreds of
  policies, many not expressible in IaC, are never exercised by the skill's.
- **Regex grading.** Loosened after reading replies many times; the controls catch false passes and
  failures only for the cases written down, and a grader fix is confirmed only on a fresh batch.
- **One sample where there could be many.** One pre-warmed distillation serves every run of a skill
  eval, and one assessment of the estate decides what applies in every run of evals 22-28 (another
  may ask other questions: one batch's never asked eval 24's).
- **Small numbers.** 3 runs per skill eval detects only large differences, and 20 per distillation
  eval only differences of a few runs; the general checks are at their ceiling. All distillation
  runs are haiku. Judge a skill-text change on about 10 runs per eval.
- **The flow evals' server** answers instantly unless told to wait, and permissions are skipped, so
  a sub-agent denied tools is never exercised.
- **Untested:** IaC other than Terraform and boto3, other clouds, `notify`-only violations, drift
  under CloudFormation/CDK/Pulumi, the bound on "untouched" changes, large refactors, multi-turn
  follow-ups, agents losing unrelated code when rewriting whole files (designed out of skill eval
  17), and a user turning guardrails off (unit tests only).
- Eval 6 rewards the X-Ray tracing that eval 16 treats as a risky fix.

Ways to do better, none started: evals written without looking at results (a held-out set);
ground-truth grading with Custodian's own IaC checker (c7n-left), or moto for the boto3 eval; an LLM
judge for the judgement-heavy brownfield cases; a posture recorded from a live deployment with
acting policies.

## The skill's evals

```
just evals OUT --yes                                  # with the skill, 3 runs per eval
just evals OUT --arms with_skill,without_skill --yes  # both arms
python3 tests/evals/grade_skill.py OUT/iter
```

Each eval is a directory, `skill/NN-name/`:
- `eval.json`: its `kind` (below), the `prompt`, the `expected_output` in words, and `start`, what
  the runner puts in place first (a repo to `copy` and `commit`, a `branch`, an `exclude` decision,
  an aged posture; `setup` says it in words);
- `checks.py`: `checks(d, t, f)` on the outputs dir, the code and the reply; `process_checks(calls,
  arm)` on the transcript; `SHOWS`, the resource types it must look up before editing;
- `test_checks.py`: its controls, outputs its checks must fail and must pass. Add one whenever a
  check changes.

What the checks share is in `skill/grading.py`. How the runner prepares caches, isolates each agent
and lays out OUT is in `run_skill.py`'s docstring. Rerunning with the same OUT finishes an
interrupted batch; distillations are cached by policy set, not prompt, so measuring a change to the
distillation prompt or the fixture needs a fresh OUT. Each run keeps its `transcript.jsonl`.

Reading the results: `grade_skill.py` sums each eval `kind` separately, never added up.
- *discriminating* evals need something only the skill supplies: at least one check only its
  knowledge passes, not one a careful model gets right anyway;
- *guards* hold only checks a run without the skill passes, to catch a change that makes things
  worse;
- *flow* evals have no arm without the skill: a check passes when the skill's process went right.

Each run also records, ungraded, `reply_words` and `out_of_scope_sentences` (about PCI where it
isn't in scope, or the sandbox-only Owner tag; an eval may declare its own `OUT_OF_SCOPE`), both of
the agent's last message, and `chained_script_calls`. The agents get none
of the runner's cloud identity (`AWS_*`, `GOOGLE_*`, `AZURE_*`, `ARM_*`, `CLOUDSDK_*`): with a
server to ask, an agent reading it would look up the runner's own accounts.

The fixture's ORG policies are things a model won't guess; without them the evals don't
discriminate (models already encrypt S3 and keep RDS private). The groups:
- **1-11**, on `tests/fixture/make_fixture.py`: greenfield tasks, a stale cache with no server (10),
  an account found in the code (11).
- **12-21, brownfield**: they start from `tests/fixture/orders-repo` committed as a git repo (21
  adds `orders-branch` on a feature branch), whose resources already break some guardrails; the
  grader compares every existing resource block with the fixture to see what else the agent changed.
- **22-28, estate** (`"fixture": "estate"`): `tests/fixture/make_estate.py`, a posture shaped like a
  real large estate's (guardrails rolled out over tag-defined waves with an opt-out tag, two GCP
  orgs, a trial remediation group whose policies delete, canaries), every name invented. They test
  the posture assessment through the agent: defaults (22, 23), an account answering the opt-out
  question (24), a project bringing in an org's collections (25), an account bringing in an ignored
  collection (26), an account found only in local Terraform state (27), and a local profile that
  isn't the deploy target (28). Their question checks read the recorded answer's text, never its id
  (ids are the assessing model's).
- **29-33, flow** (with the skill only): the agent gets a Stacklet MCP server,
  `tests/fixture/fake_stacklet_mcp.py`, serving the fixture. They cover first use with no cache
  (29), a day-old cache (30), an account whose lookup alone brings in a collection (31), a question
  bearing on other resource types than the change's (32), and a collection the platform fails to
  serve (33).

The flow evals' `start` takes away what a sync would fetch (`"cache": "none"`, `unload`,
`forget_accounts`) but keeps the assessment and distillations, so a run pays for no model call but
the agent's, and the sync's assessment step goes untested. `"mcp"` sets the server's options: `fail`
(collections it fails to serve), `pad` (KB added to every answer, so results pass the 25,000 tokens
over which Claude Code saves one to a file, as a real deployment's do: eval 29) and `delay`. Their
`server_checks(log, state, calls)` grade three records:
- what was fetched, in what order, and whether each query was the skill's own (`mcp_log.jsonl`);
- what was ingested (`cache_state.json`, from the run's cache: a mangled fetch shows there);
- when, from every tool call, a background sync's included (the transcript marks the sub-agent's;
  the other checks count only the main agent's).

The reply checks read everything the agent said, not just its last message, which after a
background sync is often a short follow-up. Ungraded: calls to the server's other tools
(`platform_graphql_info`, `get_types`), which its instructions invite and the skill never needs
(none in the runs so far).

## The posture assessment's evals

The assessment itself, posture in, judgement out, with no agent between. Each is a directory,
`assess/NN-name/`: `posture.json`, a posture from the wild, and `eval.json`, the
`shape` it exercises and any `expect`ations checked by hand.

```
just assess-evals OUT --yes      # 3 fresh runs each, plus a reassessment
python3 tests/evals/grade_assess.py OUT
```

What is checked, per run and across an eval's runs, is in `grade_assess.py`'s docstring: nothing
broad ignored or narrow applying, developers getting the same scope across runs (judged by outcome,
not by how the questions are worded), the reassessment keeping every question; imprecise questions
and coverage gaps are reported, not failed. It reports what the runs took, so models can be compared
(`--model`). A run is one model call (two if its first reply is rejected), about $0.06-0.16; one
decided by rules alone makes none. A failed run keeps its result.json: delete its directory to run
it again.

A posture from the wild keeps what the assessment judges by (group sizes and filter structure,
bindings, policy counts, regions, generic governance vocabulary), with every other name and id
replaced; the original stays outside the repo.

## The distillation's evals

These check what a distillation's bullets say about the policies they cite: policies in, bullets
out, with no agent between. Each is a directory, `distill/NN-name/`, with one `eval.json`: the
`resource_type`, its `policies` (as the platform gives them), `about` (the rule it exercises), the
`expect`ations and `controls`.

```
just distill-evals OUT --yes     # 5 fresh distillations each, on haiku
python3 tests/evals/grade_distill.py OUT
```

`run_distill.py` makes each run as `distill.py show` would, with no cache; `--model` compares
models, and `--prompt FILE` runs a template of one's own (`{rtype}` and `{bundle}` filled in) to
measure a prompt change. The checks, general and per eval, are in `grade_distill.py`'s docstring.
Each eval's `controls` (in its eval.json) are outputs, good ones and near misses, with the checks
they must fail, checked by `tests/test_run_distill.py`: every expectation is failed by one, and
every `not` pattern seen to match one. A control shows only what it shows: a lenient pattern can
still pass a wrong phrasing nobody has written down.

Each eval is set up so one rule of the prompt decides its bullets (its `about` says which).
Evals 01-23 are invented, shaped like real Custodian policies; 24-34 take their shapes from
collections in the wild, and 35-38 their `query` and `conditions` (every name, value and URL
invented). Eval 38 is a baseline the prompt misses: its bullets repeat an AMI query's parameters
instead of saying public or shared images. A run is one haiku call and a cent or two, mostly under
10 s, 90% under about 30 s, slower the more run at once.

OUT records what its runs were made with (`batch.json`), and a batch that would make any of them
differently is refused, so two prompts' runs never mix. The grader always uses the repo's current
checks, so fixing a check needs no new runs; it grades only evals whose runs read the prompt the
repo builds for them now, naming the rest.

What each change to the prompt or the evals measured, before and after, is in its commit's message
(`git log -- tests/evals/distill plugins/stacklet/skills/guardrails/scripts/distill.py`), not here,
where every later change would have to rewrite it.
