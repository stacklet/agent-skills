import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

from functools import partial
from pathlib import Path
from types import SimpleNamespace

import make_fixture as make_fixture_module
import pytest


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "plugins" / "stacklet" / "skills" / "guardrails" / "scripts"

# the stub `claude` the tests put on PATH (fake_claude below)
FAKE_CLAUDE = HERE / "fixture" / "fake_claude.py"


# the deployment make_fixture.py's responses come from (its CONSOLE), and so the cache's directory name
FIXTURE_DEPLOYMENT = "console.fixture.example"
# the scripts that take DEPLOYMENT first
DEPLOYMENT_SCRIPTS = {"assess.py", "distill.py", "ingest.py", "posture.py", "scope.py"}


def run_script(
    script, *args, cwd, cache, deployment=FIXTURE_DEPLOYMENT, stamp=True, **extra_env
):
    """Run SCRIPT with ARGS, after DEPLOYMENT when it takes one (unless DEPLOYMENT is None). With STAMP,
    the collections responses `ingest.py collections` is given are passed as copies marked as fetched
    under the cached posture (make_fixture.stamp), unless marked already, as an agent's would be."""
    if stamp and script == "ingest.py" and args[:1] == ("collections",):
        args = ("collections", *stamped(args[1:], cache, deployment))
    if script in DEPLOYMENT_SCRIPTS and deployment is not None:
        args = (deployment, *args)
    # none of the developer's own inference settings (_inference.py): tests set what they use
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("STACKLET_GUARDRAILS_")
    }
    env["STACKLET_GUARDRAILS_CACHE"] = str(cache)
    # never a real model: assess.py show makes a posture assessment when none is cached, and plain
    # `claude` may be on PATH. Tests that want the default program (the fake_claude stub) unset this.
    env["STACKLET_GUARDRAILS_INFERENCE_CMD"] = '["false"]'
    # set when tests run under Claude Code: never reach a real model
    env.pop("CLAUDE_CODE_EXECPATH", None)
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *map(str, args)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def stamped(paths, cache, deployment):
    """PATHS, each a collections response, as make_fixture.stamp copies (same names) under CACHE's
    posture for DEPLOYMENT; as they are when there is no posture to mark them with."""
    posture_db = Path(cache) / str(deployment) / "posture.db"
    if not posture_db.exists():
        return paths
    out = []
    for p in map(Path, paths):
        dest = Path(tempfile.mkdtemp(dir=Path(cache).parent)) / p.name
        out.append(make_fixture_module.stamp(p, posture_db, dest) if p.exists() else p)
    return out


def ingest_dir(run, src):
    """Load a fixture dir as an agent would: its posture, then the collection responses in it. -> the
    combined result, failing at the first step that fails."""
    steps = [("posture", src / "posture.json")]
    fetched = sorted(src.glob("collections*.json"))
    if fetched:
        steps.append(("collections", *fetched))
    out, err = "", ""
    for step in steps:
        r = run("ingest.py", *step)
        out, err = out + r.stdout, err + r.stderr
        if r.returncode:
            break
    return subprocess.CompletedProcess(r.args, r.returncode, out, err)


def make_fixture(dest):
    subprocess.run(
        [sys.executable, str(HERE / "fixture" / "make_fixture.py"), str(dest)],
        check=True,
    )


# the uuids make_fixture.py gives a collection and an account group, by name (tests import them from
# here)
cid, gid = make_fixture_module.cid, make_fixture_module.gid


def put_assessment(cache, assessment):
    """Cache ASSESSMENT (as assess.validate returns it) for the current posture in CACHE, as if the
    default command had made it: no model call."""
    import _cache
    import _inference
    import assess

    d = cache / FIXTURE_DEPLOYMENT
    db = sqlite3.connect(d / "guardrails.db")
    db.executescript(_cache.GUARDRAILS_SCHEMA)
    # stamped as Cache.open stamps it, or the next open would take it for another layout
    db.execute(f"PRAGMA user_version = {int(_cache.GUARDRAILS_LAYOUT)}")
    db.execute("ATTACH DATABASE ? AS posture", (str(d / "posture.db"),))
    cmd = _inference.Command(
        ["claude", *_inference.default_args("sonnet")], custom=False
    )
    with db:
        db.execute(
            "INSERT OR REPLACE INTO assessment VALUES (?,?,?,?,?,?,?)",
            (
                assess.posture_hash(db),
                "digest",
                json.dumps(assessment),
                cmd.recorded(),
                "Fixture Model",
                "2026-10-01T00:00:00Z",
                None,
            ),
        )
    db.close()


def org_filter(org, path=None):
    """A dynamicFilter as the platform generates it for an org's (or an OU path's) group."""
    org_term = {"single": {"name": "org", "operator": "equals", "values": [org]}}
    if path is None:
        return json.dumps(org_term)
    path_term = {
        "single": {"name": "path", "operator": "starts with", "values": [path]}
    }
    return json.dumps(
        {"multiple": {"operands": [org_term, path_term], "operator": "and"}}
    )


def edit_response(d, fn):
    """Let FN change d's collections.json (a collection.graphql response) in place: it gets the
    whole response, {"data": {"platform", "c0", ..}} (and "errors", if FN adds them)."""
    p = json.loads((d / "collections.json").read_text(encoding="utf-8"))
    fn(p)
    (d / "collections.json").write_text(json.dumps(p), encoding="utf-8")


def edit_collections(d, fn):
    """Let FN change d's collections.json in place: it gets {uuid: collection}, each with its
    policyMappings."""
    edit_response(
        d,
        lambda p: fn(
            {
                v["uuid"]: v
                for k, v in p["data"].items()
                if k != "platform" and not k.startswith("posture_") and v
            }
        ),
    )


def hold_back(d, which, first_only=False):
    """Move the collections WHICH (a test on a collection.graphql collection) picks out of d's
    collections.json into d/held/collections.json, to be loaded later. -> d/held."""
    p = json.loads((d / "collections.json").read_text(encoding="utf-8"))
    held, kept = {}, {}
    for k, v in p["data"].items():
        if k != "platform" and v and which(v) and not (first_only and held):
            held[k] = v
        else:
            kept[k] = v
    (d / "collections.json").write_text(json.dumps({"data": kept}), encoding="utf-8")
    (d / "held").mkdir()
    held["platform"] = kept["platform"]
    (d / "held" / "collections.json").write_text(
        json.dumps({"data": held}), encoding="utf-8"
    )
    return d / "held"


def policies_of(c):
    """The policies of a collection.graphql collection C."""
    return [e["node"]["policy"] for e in c["policyMappings"]["edges"]]


def edit_policies(d, fn):
    """Let FN change, in place, each policy in d's collections.json (collection.graphql's fields:
    uuid, version, name, resourceType, source, ...)."""
    edit_collections(d, lambda cs: [fn(p) for c in cs.values() for p in policies_of(c)])


@pytest.fixture(scope="session")
def pristine_fx(tmp_path_factory):
    """The generated fixture, shared by every test: read-only, use `fx` for a copy to edit."""
    fx = tmp_path_factory.mktemp("pristine") / "fx"
    make_fixture(fx)
    return fx


@pytest.fixture
def fx(pristine_fx, tmp_path):
    return shutil.copytree(pristine_fx, tmp_path / "fx")


@pytest.fixture
def cache(tmp_path):
    d = tmp_path / "cache"
    d.mkdir()
    return d


@pytest.fixture
def open_cache(cache, monkeypatch):
    """Open the fixture deployment's cache in this process, as the scripts do (Cache.open's POSTURE)."""
    import _cache

    # CACHE_DIR is read when _cache is first imported: point it at this test's cache
    monkeypatch.setattr(_cache, "CACHE_DIR", cache)
    return lambda posture=True: _cache.Cache(FIXTURE_DEPLOYMENT).open(posture=posture)


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    return d


@pytest.fixture
def run(repo, cache):
    return partial(run_script, cwd=repo, cache=cache)


@pytest.fixture
def ingest(run, pristine_fx):
    return lambda src=pristine_fx: ingest_dir(run, src)


@pytest.fixture
def collections(run):
    """Add the collection responses in a dir."""
    return lambda src: run(
        "ingest.py", "collections", *sorted(src.glob("collections*.json"))
    )


@pytest.fixture
def ingested(ingest):
    r = ingest()
    assert r.returncode == 0, r.stderr


@pytest.fixture
def scope_file(repo):
    return repo / ".stacklet" / "guardrails" / "scope.json"


@pytest.fixture
def fake_claude(tmp_path):
    """A stub `claude` on PATH that answers with a valid bullet and counts its calls."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "claude"
    shutil.copy(FAKE_CLAUDE, exe)
    exe.chmod(0o755)
    calls = tmp_path / "calls"
    calls.touch()
    return SimpleNamespace(
        exe=exe,
        calls_file=calls,
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_CLAUDE_CALLS": str(calls),
            "STACKLET_GUARDRAILS_INFERENCE_CMD": "",  # the default program: the stub
        },
        calls=lambda: len(calls.read_text().split()),
    )
