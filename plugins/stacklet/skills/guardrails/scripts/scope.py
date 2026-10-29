"""Per-repo guardrail scope: which policy collections apply to *this* codebase.

  scope.py DEPLOYMENT check              the posture assessment's questions still unanswered, and
                                         what else needs doing (only reads: `assess.py show` makes
                                         the assessment); exit 1 only if a question is unanswered,
                                         the file is for another deployment, or it is invalid
  scope.py DEPLOYMENT query              the queries fetching what this repo's scope needs and the
                                         cache lacks (posture.py's content queries): the in-scope
                                         collections, and those an unanswered question names
  scope.py DEPLOYMENT decide [--answer QUESTION=yes|no|unknown ..] [--forget-answer QUESTION ..]
                  [--account PROVIDER:KEY ..] [--forget-account PROVIDER:KEY ..]
                  [--include UUID ..] [--exclude UUID ..] --because "why"
                                         record facts (and rationale) in the repo file; with no
                                         facts, just the deployment

State lives in a committed per-repo file, .stacklet/guardrails/scope.json at the repo root (found
by walking up from the cwd, or --repo, to a .git dir or an existing scope file): *facts* with their
reasons (answers to the posture assessment's questions, the accounts this code deploys to,
include/exclude decisions), and the deployment it was scoped against, which is authoritative: the
facts are about its collections, so `decide` refuses another and `check` and distill.py ignore the
file for another. Scope is derived on every read (docs/design.md, "Scope model"): what the
assessment says applies (without one, what is bound to an all-accounts group), plus what yes-answers
add, minus what they remove, plus what looked-up accounts reach, plus includes, minus excludes,
which always win and are always surfaced. The file is untrusted repo content: validated, and
ignored with a warning when invalid.
"""

import argparse
import json
import os
import sys
import tempfile

from collections import Counter
from pathlib import Path
from typing import NamedTuple

from _cache import (
    CHECKED_POLICY_COLLECTION,
    LOADED_POLICY_COLLECTION,
    STALE_AFTER,
    TELL_USER,
    Cache,
    add_cache_args,
    add_repo_arg,
    age,
    deployment_name,
    enter_repo,
)
from _inference import made_by
from assess import cached, posture_hash, with_rules
from posture import collection_labels, content_query, format_queries


# the scope file's layout: a file of any other version was written by another version of the skill
VERSION = 5
ANSWERS = ("yes", "no", "unknown")
SCOPE_FILE = Path(".stacklet", "guardrails", "scope.json")
# Each kind of fact the scope file lists, and the fields that identify one.
FACTS = {
    "answers": ("question",),
    "accounts": ("provider", "key"),
    "decisions": ("collection",),
    "seen_bindings": ("binding",),
}


def default_scope_file(cwd=None):
    """SCOPE_FILE at the repo root, so a subdirectory doesn't get a second one. Outside a repo, the
    working directory's own: an ancestor's (`/tmp/.stacklet/...`, anyone's) mustn't choose the
    deployment and the scope."""
    cwd = Path(cwd or Path.cwd()).resolve()
    dirs = (cwd, *cwd.parents)
    root = next((d for d in dirs if (d / ".git").exists()), None)
    if root is None:
        return cwd / SCOPE_FILE
    for d in dirs:
        if (d / SCOPE_FILE).is_file() or d == root:
            return d / SCOPE_FILE


def _problem(state):
    """Why a parsed scope file is unusable, or None. Strict about shape: the code indexes into it."""
    if not isinstance(state, dict):
        return "top level must be an object"
    version = state.get("version", VERSION)
    if version != VERSION:
        return f"version {version!r} is not one this skill reads (another version wrote it)"
    if "deployment" in state and not isinstance(state["deployment"], str):
        return "'deployment' must be a string"
    if "disabled" in state and not (
        isinstance(state["disabled"], dict)
        and isinstance(state["disabled"].get("because"), str)
    ):
        return "'disabled' must be an object with string because"
    for key, fields in FACTS.items():
        if key == "decisions":
            fields = (*fields, "decision")
        if key == "answers":
            # the question's text too: an answer counts only for the question it was given to
            fields = (*fields, "answer", "name")
        items = state.get(key, [])
        if not isinstance(items, list) or not all(
            isinstance(i, dict) and all(isinstance(i.get(f), str) for f in fields)
            for i in items
        ):
            return (
                f"'{key}' must be a list of objects with string {' and '.join(fields)}"
            )
    if any(
        d["decision"] not in ("include", "exclude") for d in state.get("decisions", [])
    ):
        return "'decision' must be 'include' or 'exclude'"
    if any(x["answer"] not in ANSWERS for x in state.get("answers", [])):
        return f"'answer' must be one of {', '.join(ANSWERS)}"
    return None


def recorded_deployment(state):
    """The deployment a scope file's STATE records, as a cache name where it is one (so a hand-written
    console URL matches), else as written, truncated (it is echoed back): compared, never a path.
    None if absent."""
    d = state.get("deployment")
    if not isinstance(d, str) or not d:
        return None
    try:
        return deployment_name(d)
    except ValueError:
        return d[:100]


def other_deployment(state, deployment):
    """The deployment a scope file's STATE is recorded against, if it isn't DEPLOYMENT, else None:
    its facts were decided about that deployment's collections."""
    recorded = recorded_deployment(state)
    return recorded if recorded and recorded != deployment else None


class ScopeFile:
    """A repo's committed scope file: JSON, and untrusted repo content, so validated before use."""

    def __init__(self):
        self.path = default_scope_file()

    def read(self):
        """-> (state, problem): the parsed file ({} when absent), or ({}, a one-line reason it can't
        be used)."""
        if not self.path.exists():
            return {}, None
        try:
            state = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError) as e:  # ValueError covers bad JSON and bad UTF-8
            return {}, f"{type(e).__name__}: {e}"
        problem = _problem(state)
        return ({}, problem) if problem else (state, None)

    def load(self, strict=False):
        """The state; {} when absent. An invalid file is ignored with a warning (its problem kept as
        `problem`), unless STRICT: `decide` must never overwrite something it couldn't read."""
        state, problem = self.read()
        self.problem = problem
        if problem:
            if strict:
                sys.exit(
                    f"{self.path}: invalid scope file ({problem}); fix or remove it first"
                )
            print(
                f"warning: {self.path}: invalid scope file ({problem}); ignoring it, default "
                f"guardrails only {TELL_USER}",
                file=sys.stderr,
            )
        return state

    def _raw(self):
        """The file's top-level object unvalidated, or {} when there is none to read."""
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    @property
    def deployment(self):
        """The deployment the file was scoped against, or None, without warnings (deployments.py
        reads it to choose one). Read even from a file that fails validation: a broken file must not
        let the session drift to another tenant."""
        return recorded_deployment(self._raw())

    @property
    def disabled(self):
        """Why the user turned guardrails off for this repo, or None; read, like `deployment`, even
        from a file that fails validation: their choice holds until they ask for guardrails again."""
        d = self._raw().get("disabled")
        if not d:
            return None
        # a hand-edited entry of another shape still says off (and makes the file invalid)
        because = d.get("because") if isinstance(d, dict) else d
        return (
            because[:300] if isinstance(because, str) and because else "no reason given"
        )

    def set_disabled(self, because):
        """Record that the user turned guardrails off (BECAUSE), or with None that they turned them
        back on; everything else in the file is kept."""
        state = self.load(strict=True)
        if because is None:
            if "disabled" not in state:
                return
            del state["disabled"]
        else:
            state = {"version": VERSION, **state, "disabled": {"because": because}}
        self.write(state)

    def write(self, state):
        """Replace the file via a temp file beside it. Refuses symlinks: a repo could ship .stacklet or
        scope.json as a link, and decide would then overwrite whatever it points at."""
        path = self.path
        # .stacklet (the parent of guardrails/) is repo-supplied too
        for p in (path, path.parent, path.parent.parent):
            if p.is_symlink():
                sys.exit(f"refusing to write through symlink {p}")
        if path.exists() and not path.is_file():
            sys.exit(f"{path} exists and is not a regular file")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(state, indent=2) + "\n")
            # mkstemp makes it 0600; a committed file should get the mode any other new file gets
            umask = os.umask(0)
            os.umask(umask)
            os.chmod(tmp, 0o666 & ~umask)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def seen_now(db):
    """The bindings that exist now: {uuid: name}."""
    return dict(db.execute("SELECT uuid, name FROM binding ORDER BY name"))


# What applies without an assessment: collections bound to an all-accounts group (filter "").
ALL_ACCOUNTS_BOUND = """
SELECT DISTINCT b.collection_uuid FROM binding b
JOIN account_group g ON g.uuid = b.account_group_uuid
WHERE g.dynamic_filter = ''"""

# Collections with no membership yet, or some version in it whose content isn't cached yet.
UNLOADED = f"""
SELECT c.uuid FROM policy_collection c
WHERE NOT ({LOADED_POLICY_COLLECTION})"""

# Collections whose membership is unknown or due a check.
UNCHECKED = f"""
SELECT c.uuid FROM policy_collection c
WHERE NOT ({CHECKED_POLICY_COLLECTION})"""


class Scope:
    """A repo's scope against the posture, from a scope file's STATE and the cached assessment ROW
    (or None), with what is reported of it. Collections are uuids; the dicts map them to names."""

    def __init__(self, db, state, row):
        self.labels = collection_labels(db)
        self.assessment = with_rules(db, json.loads(row["assessment"])) if row else None
        self.made_by = f"{made_by(row)}, {row['created']}" if row else None
        # made for an earlier set of bindings (assess.cached's LATEST): stands in until a new one is
        self.assessment_stale = bool(row) and row["posture_hash"] != posture_hash(db)
        # what applies by default, plus what yes-answers add, minus what they remove
        default = applies_by_default(db, self.assessment)
        answered = read_answers(state, self.assessment)
        self.stale_answers = answered.stale
        # as recorded, valid or not: a stale one may answer a reworded question
        self.recorded_answers = {
            x["question"]: x["answer"] for x in state.get("answers", [])
        }
        by_answers = (default | answered.added) - answered.removed_by.keys()
        # plus what looked-up accounts reach
        self.placement = Placement(db, state)
        reached = self.placement.reached
        # plus includes, minus excludes, which always win
        self.in_scope, self.excluded_applicable = self._decide(
            by_answers | reached, state
        )
        # reported
        self.removed_by_answers = self._removed_by_answers(
            answered.removed_by, default | answered.added
        )
        self.from_accounts = self.by_name((self.in_scope & reached) - by_answers)
        # an earlier assessment's questions can't be answered: decide takes the current one's
        self.unanswered = (
            {}
            if self.assessment_stale
            else self._open_questions(answered.unanswered, reached, state)
        )
        self.unloaded_all = {r[0] for r in db.execute(UNLOADED)}
        # their guardrails are missing, which must never look like "none apply"
        self.unloaded = self.by_name(self.in_scope & self.unloaded_all)
        # no membership, or one older than the posture (still read)
        self.unchecked = self.by_name(
            self.in_scope & {r[0] for r in db.execute(UNCHECKED)}
        )

    def by_name(self, uuids):
        """{uuid: name} for the collections UUIDS, in name order, so reports come out the same."""
        return {u: self.labels[u] for u in sorted(uuids, key=self.labels.get)}

    def _decide(self, applies, state):
        """-> (in_scope, excluded_applicable): what APPLIES, with the decisions' includes added and
        excludes removed."""
        decisions = {d["collection"]: d for d in state.get("decisions", [])}
        in_scope = set()
        # Always shown to the agent: excluding a guardrail that applies is allowed (facts, not
        # orders) but must never be invisible.
        excluded_applicable = []
        for uuid, name in self.labels.items():
            d = decisions.get(uuid, {}).get("decision")
            if d == "exclude":
                if uuid in applies:
                    because = decisions[uuid].get("because", "")
                    excluded_applicable.append(
                        {"collection": uuid, "name": name, "because": because}
                    )
            elif d == "include" or uuid in applies:
                in_scope.add(uuid)
        return in_scope, excluded_applicable

    def _removed_by_answers(self, removed_by, would_apply):
        """What answers (REMOVED_BY: {collection uuid: the answer removing it}) take out of scope
        that WOULD_APPLY otherwise."""
        # always shown, as excludes are: allowed, but never invisible
        return [
            {
                "collection": u,
                "name": self.labels[u],
                "question": answer["question"],
                "because": answer.get("because", ""),
            }
            for u, answer in sorted(removed_by.items(), key=lambda i: self.labels[i[0]])
            if u in would_apply and u not in self.in_scope
        ]

    def _open_questions(self, unanswered, reached, state):
        """The UNANSWERED questions ({id: question}) whose answer could still change the scope."""
        # One whose answer can no longer change anything needn't be asked: accounts or includes
        # bring in all it adds (or excludes keep it out), and nothing it removes could go.
        decisions = {}
        for d in state.get("decisions", []):
            decisions.setdefault(d["decision"], set()).add(d["collection"])
        kept = reached | decisions.get("include", set())
        return {
            qid: q
            for qid, q in unanswered.items()
            if set(q["add"]) - self.in_scope - decisions.get("exclude", set())
            or set(q["remove"]) & (self.in_scope - kept)
        }

    def wanted(self):
        """The collections whose content this repo's scope needs: those in scope, and those an
        unanswered question names (which types it bears on is known only once they are loaded)."""
        out = set(self.in_scope)
        for q in self.unanswered.values():
            out |= set(q["add"]) | set(q["remove"])
        return out

    def policy_keys(self, db):
        """-> set of (policy name, version) bound by an in-scope collection."""
        return {
            (r["name"], r["version"])
            for r in db.execute("SELECT collection_uuid, name, version FROM bound")
            if r["collection_uuid"] in self.in_scope
        }

    def question_report(self, db):
        """The unanswered questions as `check` reports them: what a yes adds and removes, and the
        resource types those collections have guardrails for (incomplete while any is unloaded)."""
        types = {}
        for r in db.execute("SELECT collection_uuid, resource_type FROM bound"):
            types.setdefault(r["collection_uuid"], Counter())[r["resource_type"]] += 1
        out = {}
        for qid, q in self.unanswered.items():
            colls = [*q["add"], *q["remove"]]
            rtypes = Counter()
            for u in colls:
                rtypes.update(types.get(u, ()))
            out[qid] = {
                "ask": q["ask"],
                "answer_by": q["answer_by"],
                # names only: a question is answered by its id
                "yes_adds": list(self.by_name(q["add"]).values()),
                "yes_removes": list(self.by_name(q["remove"]).values()),
                "resource_types": dict(rtypes),
                "resource_types_complete": not any(
                    u in self.unloaded_all for u in colls
                ),
            }
        return out


def applies_by_default(db, assessment):
    """What applies before any fact: the collections ASSESSMENT says apply, or without one, those
    bound to an all-accounts group."""
    if assessment:
        return {
            u for u, c in assessment["collections"].items() if c["default"] == "applies"
        }
    return {r[0] for r in db.execute(ALL_ACCOUNTS_BOUND)}


class Answered(NamedTuple):
    """What a scope file's answers make of the assessment's questions (read_answers)."""

    added: set  # what yes-answers add
    removed_by: dict  # what yes-answers remove: collection uuid -> the (first) answer removing it
    unanswered: dict  # question id -> the question, if no answer to it counts
    stale: dict  # question id -> its text as answered, if that answer is void


def read_answers(state, assessment):
    """The Answered of a scope STATE's answers to ASSESSMENT's questions (None: no assessment)."""
    questions = {q["id"]: q for q in assessment["questions"]} if assessment else {}
    answers = {a["question"]: a for a in state.get("answers", [])}
    added = set()
    removed_by = {}
    unanswered = {}
    for qid, q in questions.items():
        answer = answers.get(qid)
        if answer is None or not answers_it(answer, q):
            unanswered[qid] = q
        elif answer["answer"] == "yes":
            added |= set(q["add"])
            for u in q["remove"]:
                removed_by.setdefault(u, answer)
    # Answers to questions this assessment doesn't ask, or asks differently, count for nothing.
    # Without an assessment they stand, unused, until there is one.
    stale = (
        {
            qid: answer["name"]
            for qid, answer in answers.items()
            if qid not in questions or not answers_it(answer, questions[qid])
        }
        if assessment
        else {}
    )
    return Answered(added, removed_by, unanswered, stale)


def answers_it(answer, question):
    """Whether ANSWER counts for QUESTION: the same id, asking the same thing (an assessment made
    again may reuse an id for another question)."""
    return answer["name"] == question["ask"]


class Placement:
    """What a repo's looked-up accounts reach, from the account facts in a scope STATE."""

    def __init__(self, db, state):
        self.reached = set()  # collections bound to the looked-up accounts' groups
        self.accounts = {}  # what `check` reports of each
        looked_up = {
            (r["provider"], r["key"]): r for r in db.execute("SELECT * FROM account")
        }
        bound_to = {}  # account group uuid -> the collections bound to it
        for g, c in db.execute(
            "SELECT account_group_uuid, collection_uuid FROM binding"
        ):
            bound_to.setdefault(g, set()).add(c)
        for f in state.get("accounts", []):
            key = (f["provider"], f["key"])
            r = looked_up.get(key)
            if r is None:
                self.accounts[":".join(key)] = {"status": "not looked up"}
                continue
            colls = set().union(*(bound_to.get(g, ()) for g in json.loads(r["groups"])))
            self.reached |= colls
            self.accounts[":".join(key)] = {
                "name": r["name"],
                "org": r["org_id"],
                "path": r["path"],
                "active": bool(r["active"]),  # an inactive account is in no groups
                # as written, "key=value", in the platform's order (keys aren't assumed unique)
                "tags": [
                    t["key"] if t.get("value") is None else f"{t['key']}={t['value']}"
                    for t in json.loads(r["tags"] or "[]")
                ],
                "looked_up": r["looked_up"],
                # a stale snapshot still counts: old groups beat none
                "status": "stale" if age(r["looked_up"]) > STALE_AFTER else "current",
                "collections": len(colls),
            }


def account_key(db, text):
    """PROVIDER:KEY -> (provider as the platform spells it, key); exits on anything else."""
    provider, _, key = text.partition(":")
    known = {
        p.lower(): p
        for (p,) in db.execute(
            "SELECT provider FROM account_group UNION SELECT provider FROM account"
        )
    }
    if not key or provider.lower() not in known:
        sys.exit(
            f"bad account {text!r}: expected PROVIDER:KEY, PROVIDER one of {sorted(known.values())}"
        )
    return known[provider.lower()], key


def merge(state, kind, *, set_, forget=(), names):
    """STATE's facts of KIND (see FACTS), with SET_ ({identity: other fields}) recorded, FORGET
    (identities) removed, and names refreshed from NAMES ({identity: name}), right after the
    identity, for people. An answer's question text says which question it answers, so NAMES
    holds it only for answers being set."""
    fields = FACTS[kind]
    by_id = {tuple(f[k] for k in fields): f for f in state.get(kind, [])}
    by_id.update(set_)
    for ident in forget:
        by_id.pop(ident, None)
    out = []
    for ident, f in by_id.items():
        rest = {k: v for k, v in f.items() if k not in (*fields, "name")}
        out.append(
            {
                **dict(zip(fields, ident)),
                "name": names.get(ident, f.get("name")),
                **rest,
            }
        )
    return out


def answer_arg(text):
    """QUESTION=ANSWER -> (question id, answer); exits on anything else."""
    qid, _, answer = text.partition("=")
    if not qid or answer not in ANSWERS:
        sys.exit(
            f"bad answer {text!r}: expected QUESTION=ANSWER, ANSWER one of {', '.join(ANSWERS)}"
        )
    return qid, answer


def cmd_decide(a, cache):
    db = cache.open()
    labels = collection_labels(db)
    for u in (a.include or []) + (a.exclude or []):
        if u not in labels:
            names = ", ".join(f"{k} ({n})" for k, n in labels.items())
            sys.exit(f"unknown collection uuid {u!r}; known: {names}")
    row = cached(db)
    questions = (
        {q["id"]: q for q in json.loads(row["assessment"])["questions"]} if row else {}
    )
    answers = [answer_arg(t) for t in a.answer or []]
    for qid, _ in answers:
        if qid not in questions:
            if row:
                known = ", ".join(questions) or "none"
            elif cached(db, latest=True):
                known = "none yet: the cached one is for earlier bindings (assess.py show makes one)"
            else:
                known = "none (assess.py show makes the assessment)"
            sys.exit(f"unknown question {qid!r}; the posture assessment's: {known}")
    scope_file = ScopeFile()
    state = scope_file.load(strict=True)
    other = other_deployment(state, cache.deployment)
    if other:
        sys.exit(
            f"{scope_file.path} is scoped against deployment {other!r}, not {cache.deployment!r}; "
            "decisions are per deployment (each has its own collections)"
        )
    because = {"because": a.because}
    answered = merge(
        state,
        "answers",
        set_={(qid,): {"answer": answer, **because} for qid, answer in answers},
        forget=[(qid,) for qid in a.forget_answer or []],
        # The question as asked, for the answers set now only: refreshing a stale one's text would
        # hand it to a reused id's new question.
        names={(qid,): questions[qid]["ask"] for qid, _ in answers},
    )
    account_names = db.execute("SELECT provider, key, name FROM account")
    accounts = merge(
        state,
        "accounts",
        set_={account_key(db, t): because for t in a.account or []},
        forget=[account_key(db, t) for t in a.forget_account or []],
        names={(p, k): n for p, k, n in account_names},
    )
    choices = {(u,): "include" for u in a.include or []}
    choices.update({(u,): "exclude" for u in a.exclude or []})
    decisions = merge(
        state,
        "decisions",
        set_={k: {"decision": v, **because} for k, v in choices.items()},
        names={(u,): n for u, n in labels.items()},
    )
    out = {"version": VERSION, "deployment": cache.deployment}
    if "disabled" in state:  # only the user's asking for guardrails again lifts it
        out["disabled"] = state["disabled"]
    out.update(
        answers=sorted(answered, key=lambda f: f["question"]),
        accounts=sorted(accounts, key=lambda f: (f["provider"], f["key"])),
        decisions=sorted(decisions, key=lambda d: d["name"] or ""),
        seen_bindings=[{"binding": u, "name": n} for u, n in seen_now(db).items()],
    )
    scope_file.write(out)
    print(f"wrote {scope_file.path}")


def cmd_check(a, cache):
    db = cache.open()
    scope_file = ScopeFile()
    state, problem = scope_file.read()
    other = other_deployment(state, cache.deployment)
    if other:
        # its facts are about another deployment's collections: what applies without them
        state = {}
    # none for these bindings: the newest stands in (assess.cached)
    row = cached(db, latest=True)
    scope = Scope(db, state, row)
    before = {b["binding"]: b.get("name") for b in state.get("seen_bindings", [])}
    now = seen_now(db)
    report = {
        "deployment": cache.deployment,
        "scope_file": scope_file.path.as_posix() if scope_file.path.exists() else None,
        "scope_file_invalid": problem or False,
        "deployment_mismatch": f"scope file is for {other!r}" if other else False,
        "assessment": (
            f"by {scope.made_by}, for earlier bindings: it stands in, its questions held back, until "
            "one is made for these"
            if scope.assessment_stale
            else f"by {scope.made_by}"
        )
        if row
        else "none made yet (a sync makes it: assess.py show): only collections bound to all "
        "accounts apply",
        "unanswered_questions": scope.question_report(db),
        "answers": scope.recorded_answers,
        "stale_answers": scope.stale_answers,
        "accounts": scope.placement.accounts,
        "from_accounts": scope.from_accounts,
        # loaded false: its guardrails are missing; checked false: its membership is unknown or
        # older than the posture (`query` fetches both)
        "in_scope": {
            u: {
                "name": n,
                "loaded": u not in scope.unloaded,
                "checked": u not in scope.unchecked,
            }
            for u, n in scope.by_name(scope.in_scope).items()
        },
        "excluded_applicable": scope.excluded_applicable,
        "removed_by_answers": scope.removed_by_answers,
        "assessment_notes": scope.assessment["notes"] if scope.assessment else [],
        "new_bindings": {u: n for u, n in now.items() if u not in before}
        if state
        else "scope file ignored"
        if other or problem
        else "no scope file yet",
        "removed_bindings": {u: n for u, n in before.items() if u not in now},
    }
    print(json.dumps(report, indent=2))
    sys.exit(1 if scope.unanswered or other or problem else 0)


def cmd_query(a, cache):
    db = cache.open()
    state = ScopeFile().load()
    if other_deployment(state, cache.deployment):
        # as check: its facts are about another deployment's collections
        state = {}
    # the cached assessment only: making one is assess.py show's
    scope = Scope(db, state, cached(db, latest=True))
    queries = format_queries(content_query(db, sorted(scope.wanted())))
    if queries:
        print(queries)
    else:
        print("nothing to fetch", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    add_cache_args(ap, ("decide", "check", "query"))
    sub = ap.add_subparsers(required=True)
    s = sub.add_parser("decide")
    s.add_argument("--answer", nargs="*", metavar="QUESTION=ANSWER")
    s.add_argument("--forget-answer", nargs="*", metavar="QUESTION")
    s.add_argument("--account", nargs="*", metavar="PROVIDER:KEY")
    s.add_argument("--forget-account", nargs="*", metavar="PROVIDER:KEY")
    s.add_argument("--include", nargs="*")
    s.add_argument("--exclude", nargs="*")
    s.add_argument("--because", required=True)
    add_repo_arg(s)
    s.set_defaults(f=cmd_decide)
    s = sub.add_parser("check")
    add_repo_arg(s)
    s.set_defaults(f=cmd_check)
    s = sub.add_parser("query")
    add_repo_arg(s)
    s.set_defaults(f=cmd_query)
    a = ap.parse_args()
    enter_repo(a)
    a.f(a, Cache(a.deployment))


if __name__ == "__main__":
    main()
