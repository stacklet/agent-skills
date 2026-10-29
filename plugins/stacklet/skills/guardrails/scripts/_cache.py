"""Where each deployment's guardrail caches live, and how the scripts read them.

Shared by every script. ingest.py writes the posture and policy content, distill.py the distillations;
the two dbs are explained in docs/design.md.
"""

import argparse
import calendar
import json
import os
import re
import sqlite3
import sys
import time

from pathlib import Path
from urllib.parse import urlsplit


# STACKLET_GUARDRAILS_CACHE: for tests and evals. Under ~/.stacklet, not ~/.cache: guardrails.db
# costs model calls to rebuild and holds tenant policy content, so cache cleaners mustn't sweep it.
CACHE_DIR = Path(
    os.environ.get("STACKLET_GUARDRAILS_CACHE")
    or Path.home() / ".stacklet" / "guardrail-cache"
)


def deployment_name(deployment):
    """The cache name for DEPLOYMENT (a console URL or a bare host): its lowercased host, port
    ignored (two local stacks on one host would share a cache). It becomes a directory, so anything
    that could point outside CACHE_DIR raises ValueError."""
    s = str(deployment).strip()
    try:
        u = urlsplit(s if "://" in s else "//" + s)
        # the port is ignored, but something that isn't one means this isn't a host ("C:\\x")
        u.port  # noqa: B018
        name = u.hostname or ""
        # a URL may have a path; a bare name is only a host, so "a/b" isn't quietly read as "a"
        if "://" not in s and (u.path or u.query or u.fragment or "@" in u.netloc):
            name = ""
    except ValueError:  # e.g. "https://[bad"
        name = ""
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) or ".." in name:
        raise ValueError(
            f"invalid deployment {deployment!r}: give the console URL, or its host "
            "(letters, digits, '.', '_' and '-')"
        )
    return name


def add_cache_args(ap, commands=()):
    """DEPLOYMENT, the required first argument of every script using a cache: which deployment a
    session uses is the agent's decision (deployments.py), never guessed. COMMANDS are the script's
    subcommands, so one in DEPLOYMENT's place is reported as DEPLOYMENT left out."""

    def deployment(v):
        if v in commands:
            raise argparse.ArgumentTypeError(
                f"missing DEPLOYMENT, the first argument (before {v!r})"
            )
        return v

    ap.add_argument(
        "deployment",
        metavar="DEPLOYMENT",
        type=deployment,
        help="the Stacklet deployment: its console URL, or the name deployments.py lists",
    )


def add_repo_arg(ap):
    """--repo (enter_repo), on each subcommand (where an agent writes it) of a script that reads the
    scope file."""
    ap.add_argument(
        "--repo",
        metavar="DIR",
        help="the repo whose scope file to read (default: the one the working directory is in)",
    )


def enter_repo(a):
    """Work from A.repo, if given: the scope file is found from the working directory, and a
    sub-agent syncing for a repo needn't be in it."""
    if getattr(a, "repo", None):
        try:
            os.chdir(a.repo)
        except OSError as e:
            sys.exit(f"--repo {a.repo}: {e.strerror}")


# What to do about a note: the tag it ends in, which SKILL.md maps to its handling.
SYNC_REFRESH = "[sync: refresh]"
SYNC_FETCH = "[sync: fetch]"
QUESTION = "[question]"
TELL_USER = "[tell the user]"
NO_POSTURE = "no posture cache at {} " + SYNC_REFRESH
# all start "no posture cache"
OLD_POSTURE = (
    "no posture cache this skill can read at {} (another version of the skill built it) "
    + SYNC_REFRESH
)
OTHER_POSTURE = "no posture cache for {} at {} (it was built from {}) " + SYNC_REFRESH
# The posture db's layout, its user_version (ingest.py stamps it); bump it with any ingest.SCHEMA
# change. One of another layout reads as no cache, which a refresh fixes, not as a crash mid-query.
POSTURE_LAYOUT = 6
# The guardrails db's layout, its user_version; bump it with any GUARDRAILS_SCHEMA change.
# Cache.open deletes a db of an older layout and leaves a newer one alone.
GUARDRAILS_LAYOUT = 3
# SQLite's files beside a db: a journal left behind would be applied to whatever db is there next
JOURNALS = ("-journal", "-wal", "-shm")


class Cache:
    """One deployment's caches, in a directory of their own so deployments never see each other's
    data: posture.db, rebuilt by each refresh, and guardrails.db, which persists."""

    def __init__(self, deployment):
        try:
            self.deployment = deployment_name(deployment)
        except ValueError as e:
            sys.exit(str(e))
        self.dir = CACHE_DIR / self.deployment
        self.posture = self.dir / "posture.db"
        self.guardrails = self.dir / "guardrails.db"

    def make_dir(self):
        """Create the deployment's directory, owner-only (it holds tenant policy content),
        tightening an older one where allowed (someone else's is theirs to secure)."""
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.dir.chmod(0o700)
        except OSError:
            pass

    def _layout_ok(self):
        """Whether the guardrails db is absent, empty or of GUARDRAILS_LAYOUT (False: an older
        layout, or not a database). Exits if it can't be read, which says nothing of its layout, and
        for a newer layout: deleting a newer skill's db would have the two delete each other's."""
        if not self.guardrails.exists():
            return True
        try:
            db = sqlite3.connect(str(self.guardrails), timeout=30)
            try:
                layout = db.execute("PRAGMA user_version").fetchone()[0]
                tables = db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
            finally:
                db.close()
        except sqlite3.OperationalError as e:  # a DatabaseError too: caught first
            sys.exit(f"cannot read the guardrail cache at {self.guardrails}: {e}")
        # not a database ("file is not a database", malformed)
        except sqlite3.DatabaseError:
            return False
        if tables and layout > GUARDRAILS_LAYOUT:
            sys.exit(
                f"the guardrail cache at {self.guardrails} was made by a newer version of the "
                "skill: update the skill (the cache is left as it is)"
            )
        return not tables or layout == GUARDRAILS_LAYOUT

    def _open_guardrails(self):
        """The guardrails db, its tables created if need be. One of an older layout, or not a
        database, is deleted with its journals and started afresh, with a note: no migrations while
        this is early, and all it held can be fetched or made again."""
        if not self._layout_ok():
            for path in (
                self.guardrails,
                *(Path(f"{self.guardrails}{x}") for x in JOURNALS),
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError as e:  # e.g. Windows, while another session has it open
                    sys.exit(
                        f"the guardrail cache at {self.guardrails} was made by an older version of "
                        f"the skill and can't be removed ({e}): close other sessions using it and "
                        "retry"
                    )
            print(
                f"note: the guardrail cache at {self.guardrails} was made by an older version of the "
                "skill: deleted; policy content and distillations will be fetched and made again",
                file=sys.stderr,
            )
        # timeout: other sessions and refreshes write this db too
        db = sqlite3.connect(str(self.guardrails), timeout=30)
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA user_version").fetchone()[0] == GUARDRAILS_LAYOUT:
            # nothing written: a posture refresh must leave this db's bytes alone
            db.executescript(GUARDRAILS_SCHEMA)
        else:
            # A new db: tables and stamp in one transaction, so no session sees unstamped tables
            # (another layout, to delete). Formatted in: PRAGMA takes no bound parameters.
            db.executescript(
                f"BEGIN IMMEDIATE;\n{GUARDRAILS_SCHEMA}\n"
                f"PRAGMA user_version = {int(GUARDRAILS_LAYOUT)};\nCOMMIT;"
            )
        return db

    def open(self, posture=True):
        """The one way into the cache: the guardrails db as main, read-write. With POSTURE, the
        posture db attached as "posture", with READ_VIEWS; exits asking for a refresh when it is
        missing, empty or of another layout. Plain paths, not read-only file: URIs, which some
        SQLite builds reject."""
        if posture and not self.posture.exists():
            sys.exit(NO_POSTURE.format(self.posture))
        self.make_dir()
        db = self._open_guardrails()
        if posture:
            db.execute("ATTACH DATABASE ? AS posture", (str(self.posture),))
            try:
                layout = db.execute("PRAGMA posture.user_version").fetchone()[0]
                tables = db.execute(
                    "SELECT COUNT(*) FROM posture.sqlite_master"
                ).fetchone()[0]
            except sqlite3.Error:  # not a database at all
                layout, tables = None, 0
            if layout != POSTURE_LAYOUT:
                db.close()
                # tables but the wrong (or no) stamp: built by another version of the skill
                sys.exit((OLD_POSTURE if tables else NO_POSTURE).format(self.posture))
            # ingest refuses another deployment's posture: this catches a db moved between dirs
            row = db.execute(
                "SELECT value FROM posture.meta WHERE key = 'deployment'"
            ).fetchone()
            built_from = row[0] if row else None
            if built_from != self.deployment:
                db.close()
                sys.exit(
                    OTHER_POSTURE.format(self.deployment, self.posture, built_from)
                )
            db.create_function(
                "action_names",
                1,
                lambda s: json.dumps(action_names(s)),
                deterministic=True,
            )
            db.executescript(READ_VIEWS)
        return db


# The deployment's persistent data, created by every connection (Cache.open). Policy versions are
# immutable, so no derived columns (they would go stale). An account row is one lookup's snapshot,
# replaced whole by the next; so is a collection's membership (collection_policy and membership).
GUARDRAILS_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy (uuid TEXT, version INT, name, provider, resource_type, mode,
    severity, compliance JSON, source_json, PRIMARY KEY (uuid, version));
CREATE TABLE IF NOT EXISTS distillation (collection TEXT,  -- its uuid
    resource_type TEXT,
    policy_hash TEXT,  -- distill.policy_hash: the exact policy versions distilled
    text TEXT,
    inference_cmd JSON,  -- the argv that made it, for people (not part of the key)
    model TEXT,  -- the model's own name for itself (distill.split_model), or NULL if it gave none
    created TEXT,
    stats JSON,  -- what making it took: seconds, tokens, attempts (_inference.combined)
    PRIMARY KEY (collection, resource_type, policy_hash));
CREATE TABLE IF NOT EXISTS assessment (posture_hash TEXT PRIMARY KEY,  -- assess.posture_hash
    digest TEXT,  -- what the model was shown (posture.digest)
    assessment JSON,  -- validated (assess.validate), collections by uuid
    inference_cmd JSON, model TEXT, created TEXT, stats JSON);  -- as for distillation
CREATE TABLE IF NOT EXISTS account (provider TEXT, key TEXT, name, path, org_id, active INT,
    groups JSON,  -- the uuids of the account groups it is in
    looked_up TEXT,  -- when this snapshot was taken
    tags JSON,  -- its tags as the platform gives them: [{"key", "value"}], or NULL for none
    PRIMARY KEY (provider, key));
CREATE TABLE IF NOT EXISTS collection_policy (collection_uuid TEXT, policy_uuid TEXT,
    policy_version INT, PRIMARY KEY (collection_uuid, policy_uuid));
CREATE TABLE IF NOT EXISTS membership (collection_uuid TEXT PRIMARY KEY,
    posture TEXT);  -- the refresh id (posture.meta) of the posture its rows were fetched under
"""


# How readers see policies: `policy`, the versions some collection binds (the db keeps versions no
# longer bound, which must not read as guardrails); `bound`, one row per version a collection binds.
# A membership older than the posture is still read until checked again (CHECKED_POLICY_COLLECTION).
# TEMP, per connection: they call action_names, a Python function other readers of the file lack.
READ_VIEWS = """
CREATE TEMP VIEW policy AS
SELECT p.*, action_names(p.source_json) AS actions
FROM main.policy p
WHERE EXISTS (SELECT 1 FROM main.collection_policy cp
              WHERE cp.policy_uuid = p.uuid AND cp.policy_version = p.version);

CREATE TEMP VIEW bound AS
SELECT cp.collection_uuid, p.*
FROM main.collection_policy cp
JOIN policy p ON p.uuid = cp.policy_uuid AND p.version = cp.policy_version;
"""


# SQL on the policy_collection row aliased `c`. MEMBERSHIP_POSTURE: the refresh id its membership was
# fetched under, NULL if it never was.
MEMBERSHIP_POSTURE = (
    "(SELECT m.posture FROM main.membership m WHERE m.collection_uuid = c.uuid)"
)
# LOADED_POLICY_COLLECTION: its membership is fetched and every version in it has content, or it has
# none and the posture says it is empty. Unfetched means guardrails missing, never "no policies".
LOADED_POLICY_COLLECTION = f"""(CASE WHEN {MEMBERSHIP_POSTURE} IS NULL THEN c.policy_count = 0
    ELSE NOT EXISTS (SELECT 1 FROM main.collection_policy cp
        WHERE cp.collection_uuid = c.uuid AND NOT EXISTS (SELECT 1 FROM main.policy p
            WHERE p.uuid = cp.policy_uuid AND p.version = cp.policy_version)) END)"""
# CHECKED_POLICY_COLLECTION: its membership was fetched under this posture, or it needs none. The
# posture can't say what changed in a collection, so a refresh makes every membership due again.
CHECKED_POLICY_COLLECTION = f"""COALESCE(
    {MEMBERSHIP_POSTURE} = (SELECT value FROM posture.meta WHERE key = 'refresh_id'),
    c.policy_count = 0)"""


def alias_of(collection):
    """The alias a content query gives COLLECTION's line: from its uuid, so ingest can name one that
    came back null or failed."""
    return "c_" + re.sub(r"\W", "_", collection)


# a day: how old a posture or account lookup gets before a session refreshes it
STALE_AFTER = 24 * 3600
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def now_utc():
    return time.strftime(TIME_FORMAT, time.gmtime())


def age(when):
    """Seconds since WHEN, a TIME_FORMAT timestamp."""
    return time.time() - calendar.timegm(time.strptime(when, TIME_FORMAT))


def posture_age(db):
    """(seconds since the posture was ingested, when) from an open posture db."""
    when = db.execute("SELECT value FROM meta WHERE key = 'refreshed_at'").fetchone()[0]
    return age(when), when


def policy_doc(source_json):
    """The single policy dict from a Policy.source JSON string (bare policy or {"policies": [..]})."""
    try:
        doc = json.loads(source_json)
    except (TypeError, ValueError):
        return {}
    if isinstance(doc, dict) and "policies" in doc:
        doc = (doc["policies"] or [{}])[0]
    return doc if isinstance(doc, dict) else {}


def action_names(source_json):
    """The names of a policy's actions, in order, without repeats ([]: it only reports). Not
    classified: the agent reads "notify" or "delete" better than any classification would. An
    action is a bare name or a dict with a `type`; mark-for-op's deferred `op` is named too
    ("mark-for-op:delete")."""
    names = []
    for a in policy_doc(source_json).get("actions") or []:
        if isinstance(a, dict):
            name = str(a.get("type") or "?")
            if name == "mark-for-op" and a.get("op"):
                name = f"mark-for-op:{a['op']}"
        else:
            name = str(a)
        if name not in names:
            names.append(name)
    return names
