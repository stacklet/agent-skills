---
name: guardrails
description: Check the organisation's Stacklet cloud-governance policies before writing or changing code that defines or creates cloud resources. Use whenever you are about to create or modify cloud resources — in infrastructure-as-code (Terraform/OpenTofu aws_*/google_*/azurerm_*, CloudFormation/SAM, CDK, Pulumi, Bicep, Crossplane, Kubernetes manifests for cloud-backed resources) or at runtime through SDK/CLI calls (boto3 create_bucket/create_db_instance/run_instances, aws/gcloud/az CLI scripts, CDK custom resources) — for S3 buckets, databases, disks, security groups, IAM, queues, VMs and the like, even if the user never mentions Stacklet, policies, compliance or guardrails. Surfaces which deployed policies apply to those resource types so the code avoids violations (public buckets, unencrypted storage, open ingress, missing tags, etc.) before it ever reaches an account. Not for application code that merely uses existing cloud resources (uploading to a bucket, querying a table), or for questions about Stacklet itself.
---

# Stacklet guardrails

The user's organisation deploys policies to its cloud accounts through Stacklet; resources that
violate them get flagged (or remediated) after deployment. Catching that while writing the
infrastructure code is far cheaper than finding out later, so before defining or changing a
cloud resource, look up which guardrails apply to that resource type and write code that
complies.

Guardrails are **facts about the environment, not orders.** Tell the user what applies and what
you did about it. If they have a good reason to violate one, say what the consequence will be
and do what they ask. Never refuse or stall a task because of a guardrail, and never block
because this skill's data is unavailable: say so in a line and carry on.

## The goal

Before you write or change code that defines a cloud resource, the guardrails that apply to its
resource type in this repo are in your context: `distill.py <deployment> show <type>`, read from a
cache holding everything in this repo's scope as the platform has it now. Everything below serves
that. The cache is filled through the Stacklet MCP server, which is slow, so start filling it early
and in the background; whatever still keeps it short of complete and current is said by the scripts
and handled as below, never by stalling the task.

## Running the scripts

```
python3 ${CLAUDE_SKILL_DIR}/scripts/<script>.py ...
```

(`python` or `py -3` on Windows if `python3` isn't there.) The scripts use only the Python 3
standard library, so there is nothing to install. Run each one as its own plain command (no pipes or
`&&` chains) so a single permission rule covers every call. Run them from anywhere inside the repo
you're working on: the scope file is found at the repo root (`.stacklet/guardrails/scope.json`),
never created in a subdirectory.

Every script but `deployments.py` takes the Stacklet deployment as its first argument, written
`<deployment>` below: the name `deployments.py` gives in step 1 (the console URL's host). Everything
cached for a deployment lives in its own directory under `~/.stacklet/guardrail-cache/` (root
overridable with `STACKLET_GUARDRAILS_CACHE`), and `.stacklet/guardrails/scope.json` records which
deployment a repo was scoped against.

Messages on stderr starting `note:` or `warning:` without a tag are for the user too; pass the
substance on. Those ending in a tag in brackets say what to do about them (step 3).

## 1. As soon as the task touches cloud resources

Once per session, before reading much of the code, so that what has to be fetched arrives while you
work:

1. **Choose the deployment.** Run `platform_graphql_query` with the query in
   `${CLAUDE_SKILL_DIR}/platform/deployment.graphql` on every Stacklet MCP server available (tools
   named `mcp__<server>__platform_graphql_query` and so on; leave out any that fail), then
   `deployments.py --console <url> [<url> ..]` (no `--console` if none answered; add `--enable` if
   the user asked for guardrails, by name or `/stacklet:guardrails`). It prints `use`, the
   deployment, and `reason`. Pass that deployment to every script from then on, and make every MCP
   call on the server whose console it is (`ingest.py` refuses another deployment's data). If
   `use` is null, or `reason` says the scope file records it or that there is no refresh, follow
   `${CLAUDE_SKILL_DIR}/references/deployment.md`.
2. **See what the cache lacks:** `distill.py <deployment> index`. It lists the resource types with
   guardrails in this repo's scope (cheap, no model call), and its notes say what keeps that short:
   no cache at all ("no posture cache"), an old one, collections not fetched, accounts not looked up.
3. **Sync** if any note is tagged `[sync: refresh]` or `[sync: fetch]`: in `refresh` mode if any
   says refresh (or the user says policies have changed), else `fetch` (below).
4. **Get on with the task** while it runs: read the code you will change, plan the change, and
   find the accounts it deploys into (step 2).

### Syncing

A sync fetches, through the deployment's MCP server, what this repo's guardrails need: the posture
(`refresh` mode only), the posture assessment, the repo's account lookups, and the membership and
content of the collections in its scope and of those its unanswered questions name. It is mechanical
and slow (seconds per batch of collections, minutes when the assessment is made), so run it in the
background where you can:

- If you can start a sub-agent that runs in the background with this session's tools (Claude
  Code's Agent tool, `run_in_background`), do: ask it to read
  `${CLAUDE_SKILL_DIR}/references/sync.md` (give the path written out in full) and follow it with
  `<skill>` = `${CLAUDE_SKILL_DIR}`, `<deployment>`, `<server>` (the MCP server whose console is
  the deployment's), `<repo>` (the repo root, as an absolute path) and `<mode>`, and to end with
  its report. If the report says tools were denied it (a sub-agent can't answer a permission
  prompt), follow `sync.md` yourself instead.
- Otherwise, or for a small follow-up you would only wait on, follow
  `${CLAUDE_SKILL_DIR}/references/sync.md` yourself, with the same values.

Run one sync at a time. When it reports, pass on what it lists `for the user`, and any collection
`not loaded` (its guardrails are missing). An account the platform doesn't know: tell the user, and
`scope.py <deployment> decide --forget-account <PROVIDER:KEY> --because "the platform doesn't know
it"`. Then `show` again each resource type you have already looked at, and tell the user about any
guardrail that changes code you wrote.

If the deployment has no MCP server this session, nothing can be synced: say in a line which
guidance is missing or old, and carry on with the cache as it is. A posture assessment that is
missing, or made for earlier bindings, needs no server: `assess.py <deployment> show` makes one (a
model call of up to 10 minutes).

## 2. Find the accounts the change deploys into

Before the first `show`, whether or not a sync is running: whatever is bound to an account's groups
applies to it, so the accounts can bring in guardrails and settle questions before anyone is asked,
and are the only way in for collections bound to a few specific accounts. Working out which
accounts the change deploys into is your job: the scripts read nothing from the repo or the
machine.

- Look wherever they show: Terraform `provider` blocks (`allowed_account_ids`, an `assume_role`
  ARN's account id), tfvars and backend config, CDK `env`, a GCP `project`, an Azure
  `subscription_id`, CI/deploy config, and the local environment (a Terraform state's resource ARNs,
  the cloud CLI profile or credentials in use, `gcloud`/`az` configuration), or something the user
  said.
- Then judge whether each one is in play for this change: where this code will deploy. A profile
  on this machine, a workspace's state, an account in a comment or another environment's tfvars may
  be nothing to do with it. Record only those you judge in play, and say where you found each in
  `--because`; when it is unclear, one short question settles it. Don't interrogate the user for a
  complete list (the final account may not even exist yet).
- Record them: `scope.py <deployment> decide --account AWS:<id> [AWS:<id2> ..] --because "<where
  you found it>"`, even with no Stacklet MCP server this session (a lookup may be cached already).
  It needs a cached posture, which a first sync stores in its first seconds: until then `decide`
  says "no posture cache", which is no reason for another sync; try again shortly, and the running
  sync looks the accounts up.

An account adds nothing until it is looked up, which a sync does: `show` says so (`[sync: fetch]`).

## 3. Before writing each resource type

`distill.py <deployment> show <type>` (step 4 says how to read it), and write to its guidance. Then
handle its notes by their tags:

- **`[sync: refresh]`**, **`[sync: fetch]`**: the cache is short of what applies (no or an old
  posture; collections not loaded, or read from a membership older than the posture; accounts not
  looked up; no posture assessment yet). Sync, unless one is running.
  - If the note is a `warning:` (collections not loaded, accounts not looked up, no posture
    assessment yet), or `show` failed for want of a cache, guardrails that could change the code
    may be missing: wait for the sync before writing that type, doing other work meanwhile, then
    `show` again. Anything else (an old posture, membership or assessment): write with what `show`
    gave, and `show` again when the sync reports.
  - Don't sync again for what the last sync reported it couldn't do (a collection it couldn't
    load): tell the user what is missing, and write with what `show` gave. Something new since (an
    account you recorded, say) is worth a sync in `fetch` mode: usually a lookup and a few
    collections, quick enough to do yourself.
- **`[question]`**: an unanswered question about the repo could change this type's guardrails.
  Settle it yourself where you can: from what the user has told you or the code shows, or from the
  accounts you know (`scope.py <deployment> check` lists each looked-up one's org, OU path and
  tags). If the note says an account isn't looked up yet and a sync can look it up, let it. Ask the
  user, about the code, only what that leaves open. Record the answer either way
  (`${CLAUDE_SKILL_DIR}/references/scope.md`, "Questions"); then `show` again. A question clearly
  unrelated to the code (a payments question, for an image thumbnailer) needs no asking.
- **`[tell the user]`**: the repo's scope file switches off guardrails that would apply, or can't be
  used. Say so in a line, once: the user is allowed to switch guardrails off, but it must never be
  invisible. What to do about an invalid scope file, or one for another
  deployment: `${CLAUDE_SKILL_DIR}/references/scope.md`.

An unloaded collection means its guardrails are *missing*, not that none apply: never present what
`show` gives with collections unloaded as complete.

The user can always overrule what applies, and `scope.py <deployment> check` reports the whole scope
(what applies and why, every question, every recorded fact): both are in
`${CLAUDE_SKILL_DIR}/references/scope.md`.

## 4. Reading the guidance

Cheapest first (`index` and `show` only ever show the guardrails in this repo's scope):

- `distill.py <deployment> index [--provider AWS]`: resource types with guardrails, the worst
  severity, and the Custodian actions their policies take, with counts. Pick the provider from the
  code (`aws_*`/boto3 is AWS, `google_*` GCP, `azurerm_*` Azure).
- `distill.py <deployment> show aws.rds`: distilled bullets for one resource type, one
  `## Collection:` section per in-scope collection (a guardrail bound by several collections
  repeats; that is fine). The first call for a collection's section takes ~30s (it runs a model to
  summarise its policies; several run at once, and a rejected summary is asked for again), so run it
  with a command timeout of 10 minutes; later calls are cached. Each bullet names its policies as
  `name@version` (what `source` takes), usually at its end. It says so explicitly if nothing is in
  scope. `--redistill` redoes the cached sections (keeping each old one if redoing it fails); only
  when the user asks, since any distillation of the same policies is usable. The model runs through
  `claude -p`; for other agents, see `${CLAUDE_SKILL_DIR}/references/inference.md`. If `show` can't
  distil (its warnings say the model command failed), `show <type> --dry-run` prints, for each
  section not yet distilled, the prompt the model would get (the policies as JSON, under its
  instructions): work from the policies, and tell the user once that the guidance is undistilled.
- `distill.py <deployment> source NAME@VERSION`: the raw policy behind a ref (exactly as the bullet
  names it), when you need exact detail.

Actions say what the platform does to a non-compliant resource: none means it only reports, `notify`
tells someone, and `tag`, `mark-for-op:stop` (a stop scheduled for later), `set-encryption`, `stop`
or `delete` act on the resource itself. When a guardrail acts on what this code creates, say so to
the user: it may be changed or removed outside their control.

### Judge the change, not the resource

Most infrastructure work changes what already exists, and existing resources often break
guardrails already. So compare what will be deployed after the change with what is deployed now
(what `terraform plan` would show), and sort the guardrails that apply into three kinds. What is
deployed now is what reached the default branch, not everything in the files: a resource added on
this branch (`git diff <default>...HEAD` shows which) or earlier in this session has never been
deployed, so it is new, and fixing it costs nothing live.

- **Introduced**: violations that would exist only because of this change.
  - Everything the change creates is new, and so is everything it replaces (making a queue FIFO,
    say, replaces it): give it every guardrail that applies, even when its neighbours don't comply
    (don't copy their violations).
  - Introduce none unless the user insists; then say what the guardrail will do.
  - Replacing makes a fix free for the resource, not for what depends on it (a customer-managed key
    needs every reader and writer granted; a dead-letter queue changes retries): say what those
    need.
- **Removed**: violations on what the change touches that it could also fix.
  - Fixing a live resource has costs of its own: some fixes replace it (encrypting an existing RDS
    instance or EBS volume), meaning downtime or lost data, and others change behaviour something
    may depend on (gp2 to gp3 lowers a large volume's default throughput; a customer-managed key
    needs every reader and writer granted).
  - Make a fix along with the change only when it is in place, harmless and needs nothing you don't
    already have (tags whose values the repo holds, say), and say so. Adding a missing tag fills a
    gap; changing one overrides a decision someone made.
  - A value that is a judgement about the data, such as a classification, is different even when
    the repo has a default: once it holds a value the guardrail accepts, the platform stops
    reporting the gap, so a wrong guess hides itself. If you set one, say so.
  - Any other fix is a trade-off for whoever owns the system: say what fixing it would involve.
- **Untouched**: violations on what the change edits or sits beside (the same file or module) that
  stay as they are.
  - The platform already reports them, so naming them in a line is enough; don't explain each one,
    and don't audit the rest of the repo.
  - When the same gap runs through the repo and one setting would fix it everywhere (provider
    `default_tags` for a missing tag, say), mention that rather than fixing resources one by one: it
    changes every resource it reaches.

The exception is a guardrail whose actions change the resource itself (not `notify`, which only
tells someone, nor `mark-for-op` before its operation runs). If an existing resource violates one,
the platform has probably already changed the deployed resource, so the code no longer describes
it. What a deploy then does depends on the tool: Terraform and OpenTofu put the settings they manage
back on every apply, even of an unrelated edit (and recreate what was deleted), and the platform
changes them again; CloudFormation, CDK and Pulumi leave the change in place until that setting is
edited (Pulumi also on a refresh); a stopped resource stays stopped. If the change touches that
resource, tell the user plainly, and that settling it means changing the code to match or exempting
the resource on the platform. If it doesn't, a line is enough on what deploying this change will do
to it, if anything.

### Writing the code

`show` takes Stacklet's `provider.type` names, to which the Terraform/CloudFormation/etc. resource
names map (`aws_s3_bucket` -> `aws.s3`, `aws_db_instance` -> `aws.rds`); use `index` to find the
right key if unsure.

Runtime provisioning counts too. When code creates resources by calling an API (boto3
`create_bucket`, `create_db_instance`, `run_instances`; a CLI script; a CDK custom resource),
the same guardrails apply to the resource that will exist afterwards. Guidance is phrased as
resource attributes, so translate it into the call's parameters: put encryption and tags in the
create call where the API accepts them (`create_db_instance(StorageEncrypted=True, Tags=...)`),
and where it can't (S3's `create_bucket`), make the follow-up calls (`put_public_access_block`,
`put_bucket_encryption`, `put_bucket_tagging`) immediately after, in the same function, so the
resource isn't left non-compliant if the script stops midway. Code that merely uses an existing
resource (upload to a bucket, query a table) isn't covered; leave it alone.

Afterwards, summarise in those terms, briefly: what you expect the plan to show for each resource
the change touches (created, changed in place, replaced, destroyed), what the change introduces
(ideally nothing) and fixes, what it leaves untouched (a line), and any guardrail you deliberately
did not follow and why. Keep it short; the user wants working infrastructure, not a policy lecture.

## Known limitations

The cache is only refreshed when a session uses it.
