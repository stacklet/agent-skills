# Which guardrails apply to this repo

Platforms bind policies to groups of accounts, and a repo deploys to only some of them, so the
guardrails are filtered through a per-repo scope file, `.stacklet/guardrails/scope.json` (meant to
be committed; mention that when you first write it). It records *facts* with their reasons, and
scope follows from them. `index` and `show` always apply it; `scope.py <deployment> check` reports
it whole.

The base is the deployment's **posture assessment**: a model's reading of the whole posture (which
collections are bound to which account groups, and what defines those groups), made once per change
of bindings and shared by every repo on the deployment. It says which collections apply by default,
which to ignore, and asks yes/no questions that settle the rest ("does this code deploy into a GCP
project in the studio.example org?"). A sync makes it, with `assess.py <deployment> show` (a model
call of 10s to a few minutes, so a command timeout of 10 minutes); with no MCP server, run that
yourself. It prints the assessment with its reasons, making one if there is none (`--reassess`
makes it again). `scope.py <deployment> check` only reads, so it is safe at any time, a sync running
or not.

## Accounts

Finding the accounts a change deploys into, judging which are in play, and recording them is
SKILL.md step 2. Each recorded account is reported by `check` (`accounts`, below); an answer by
account uses what its lookup gives (org, OU path, tags).

## The check report

`scope.py <deployment> check` prints a JSON report. It exits 1 when there is a question to ask, or
the scope file is invalid or for another deployment (both below), and 0 otherwise:

- `assessment`: who made it and when. If it says `none made yet`, only collections bound to every
  account apply: a sync makes it (if its report says making it failed, tell the user why in a line).
  If it says it is for earlier bindings, it stands in (and its questions are held back) until one
  is made for these.
- `unanswered_questions`: each question (by id) with `ask`, the collections a yes adds (`yes_adds`)
  and removes (`yes_removes`), by name, `answer_by`, and the `resource_types` those collections have
  guardrails for (`resource_types_complete` false: some of them aren't loaded yet, so there may be
  more). A question whose answer can no longer change anything is left out (an account already
  brings in all it would add, say). `show` names the ones that bear on its resource type.
- `answers`: the recorded ones. `stale_answers`: answers to questions the current assessment no
  longer asks, or asks differently under the same id (the bindings changed and it was made again):
  they count for nothing (below).
- `accounts`: the recorded accounts, each with when it was looked up: `not looked up` (e.g. after
  the scope file arrived from a colleague) adds nothing until a sync looks it up; `stale` still
  counts, and the next sync looks it up again.
- `from_accounts`: collections that apply only because they reach those accounts.
- `in_scope`: every collection that applies, by uuid, with its `name`, whether it is `loaded` (one
  that isn't has its guardrails missing until a sync fetches it) and whether its membership is
  `checked` under the current posture (one that isn't may be behind the platform's until a sync
  checks it again).
- `assessment_notes`: what the assessing model noticed about the posture (contradictions, gaps);
  mention one only if it bears on the change.
- `excluded_applicable`, `removed_by_answers`: guardrails the scope file switches off (below).
- `scope_file_invalid`, `deployment_mismatch`: the file can't be used (below).
- `new_bindings` and `removed_bindings` (bindings added or gone since the file was last written) are
  informational and need no answer; `deployment` and `scope_file` say which deployment and file were
  read.

Collections and accounts are identified by the uuids and `PROVIDER:KEY`s `check` reports; names are
for talking to the user.

## Questions

Ask only questions that could change what you are writing now: `show` names those for its resource
type (`[question]`), once what they bear on is known (their collections loaded). One clearly
unrelated to the code (a payments question, for an image
thumbnailer) needs no question either. The rest stay unanswered and come up again when the code
touches their types.

- Whatever `answer_by` says, what the user has already told you or the code shows can settle it
  (say which in `--because`).
- `answer_by` `account`: settle it from what you know about the target accounts (`accounts` in the
  check report lists each looked-up one's name, org, OU path and tags; the groups it is in decide
  what reaches it) where you can, and say how in `--because`. `user` or `either`: ask **about the
  code**, as the question is phrased, never about policy names.
- Record each answer: `scope.py <deployment> decide --answer <id>=yes [<id2>=no ..] --because
  "<what the user said, or how you know>"`. "Not sure" is `<id>=unknown`: it adds nothing, but stops
  the question being asked again. If the user is in a hurry or declines, proceed and name what the
  unanswered questions would have added; never assume a yes.
- A stale answer (the assessment was made again, and asks differently; `show` names the recorded
  answer and its wording beside the question): if the question asks the same thing, answer it from
  what was recorded (`--because` saying so); answering it under the same id replaces the stale
  answer. Then `--forget-answer` only the stale ones (`check`'s `stale_answers`) whose ids you
  didn't just answer.

## Overrides

The user can always overrule: `scope.py <deployment> decide --include <uuid> [..]` for a collection
they say applies whatever the answers, `--exclude <uuid>` for one they want left out. Whatever the
exit code, if `excluded_applicable` or `removed_by_answers` is non-empty (`index` and `show` note
each, `[tell the user]`), tell the user that the repo's scope file switches off guardrails that
would otherwise apply (by an exclude, or by an answer: the question and its `because` are given),
and why: they are allowed to, but it should never be invisible.

## A scope file that can't be used

- `scope_file_invalid`: the committed file can't be read. Tell the user, work from the defaults
  only, and don't try to overwrite it (`decide` refuses until it is fixed).
- `deployment_mismatch`: the file was scoped against a different Stacklet deployment than the one
  being used, so it was ignored (defaults only): see `${CLAUDE_SKILL_DIR}/references/deployment.md`.
