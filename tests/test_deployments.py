"""Deployment namespacing: every script names its deployment, caches are isolated per deployment,
responses from another deployment are refused, a repo's scope file records which deployment it was
scoped against, and unsafe names are refused."""

import json
import shutil
import sqlite3

from functools import partial
from types import SimpleNamespace

import pytest

from conftest import (
    DEPLOYMENT_SCRIPTS,
    FIXTURE_DEPLOYMENT,
    cid,
    ingest_dir,
    make_fixture,
    run_script,
)


ACME = "console.acme.example"


def write_scope(repo, state):
    """Give REPO a scope file holding STATE (text as it is, anything else as JSON)."""
    (repo / ".stacklet" / "guardrails").mkdir(parents=True)
    (repo / ".stacklet" / "guardrails" / "scope.json").write_text(
        state if isinstance(state, str) else json.dumps(state)
    )


def read_scope(repo):
    return json.loads((repo / ".stacklet" / "guardrails" / "scope.json").read_text())


@pytest.fixture(scope="module")
def deployments(tmp_path_factory):
    """The fixture ingested as itself, and as 'acme' (another console) without the PCI collection."""
    tmp = tmp_path_factory.mktemp("deployments")
    cache, fx, fx_acme = tmp / "cache", tmp / "fx", tmp / "fx_acme"
    make_fixture(fx)
    shutil.copytree(fx, fx_acme)
    d = json.loads((fx_acme / "posture.json").read_text())
    b = d["data"]["bindingPosture"]
    b["policyCollections"] = [
        c for c in b["policyCollections"] if c["name"] != "PCI-DSS AWS"
    ]
    keep = {c["uuid"] for c in b["policyCollections"]}
    b["bindings"] = [x for x in b["bindings"] if x["policyCollection"]["uuid"] in keep]
    (fx_acme / "posture.json").write_text(json.dumps(d))
    for name in ("posture.json", "account_0.json", "collections.json"):
        with_console(fx_acme / name, fx_acme / name, f"https://{ACME}/")

    for deployment, src in ((FIXTURE_DEPLOYMENT, fx), (ACME, fx_acme)):
        run = partial(run_script, cwd=tmp, cache=cache, deployment=deployment)
        r = ingest_dir(run, src)
        assert r.returncode == 0, r.stderr
    return SimpleNamespace(tmp=tmp, cache=cache, fx=fx, fx_acme=fx_acme)


# Every kind of response ingest takes, with the fixture's file of it: each names its deployment, the
# collections (the policy content itself) included.
RESPONSES = [
    ("posture", "posture.json"),
    ("accounts", "account_0.json"),
    ("collections", "collections.json"),
]


def run_in(deployments, *args, deployment=FIXTURE_DEPLOYMENT, cwd=None):
    return run_script(
        *args,
        cwd=cwd or deployments.tmp,
        cache=deployments.cache,
        deployment=deployment,
    )


@pytest.fixture(scope="module")
def acme_repo(deployments):
    """A repo scoped against the acme deployment."""
    repo = deployments.tmp / "repo"
    repo.mkdir()
    r = run_in(
        deployments,
        "scope.py",
        "decide",
        "--exclude",
        cid("CIS AWS Baseline"),
        "--because",
        "t",
        deployment=f"https://{ACME}/",
        cwd=repo,
    )
    return SimpleNamespace(path=repo, decided=r)


def has_pci(r):
    return "PCI-DSS AWS" in r.stdout


def test_each_deployment_gets_its_own_directory(deployments):
    assert (deployments.cache / FIXTURE_DEPLOYMENT / "posture.db").exists()
    assert (deployments.cache / ACME / "posture.db").exists()
    assert not (deployments.cache / "posture.db").exists()


def test_each_deployment_sees_only_its_own_collections(deployments):
    assert has_pci(run_in(deployments, "posture.py"))
    assert not has_pci(run_in(deployments, "posture.py", deployment=ACME))


@pytest.mark.parametrize(
    "spelling",
    [
        ACME,
        f"https://{ACME}",
        f"HTTPS://{ACME.upper()}/some/path",
        f"https://{ACME}:443/",
        f"{ACME}:8080",
    ],
)
def test_a_console_url_and_its_host_name_the_same_cache(deployments, spelling):
    # a port is ignored: deployments are told apart by host
    r = run_in(deployments, "posture.py", deployment=spelling)
    assert r.returncode == 0, r.stderr
    assert not has_pci(r)


@pytest.mark.parametrize("script", sorted(DEPLOYMENT_SCRIPTS))
def test_deployment_is_required(deployments, script):
    r = run_in(deployments, script, deployment=None)
    assert r.returncode != 0
    assert "DEPLOYMENT" in r.stderr


@pytest.mark.parametrize(
    "cmd",
    [
        ("scope.py", "check"),
        ("distill.py", "index"),
        ("ingest.py", "posture", "x"),
        ("assess.py", "show"),
    ],
)
def test_a_subcommand_in_deployments_place_says_deployment_is_missing(deployments, cmd):
    r = run_in(deployments, *cmd, deployment=None)
    assert r.returncode != 0
    assert "missing DEPLOYMENT" in r.stderr


@pytest.mark.parametrize(("kind", "name"), RESPONSES)
def test_ingest_refuses_a_response_from_another_deployment(
    deployments, tmp_path, kind, name
):
    # e.g. fetched through the wrong one of several Stacklet MCP servers
    cache = tmp_path / "cache"
    shutil.copytree(deployments.cache, cache)
    before = (cache / FIXTURE_DEPLOYMENT / "posture.db").read_bytes()
    r = run_script(
        "ingest.py",
        kind,
        deployments.fx_acme / name,
        cwd=tmp_path,
        cache=cache,
    )
    assert r.returncode != 0
    assert f"from deployment {ACME!r}" in (r.stdout + r.stderr)
    assert (cache / FIXTURE_DEPLOYMENT / "posture.db").read_bytes() == before


@pytest.mark.parametrize(("kind", "name"), RESPONSES)
def test_a_refused_ingest_creates_no_cache(deployments, tmp_path, kind, name):
    # a mistyped DEPLOYMENT must not leave a directory for deployments.py to offer
    cache = tmp_path / "cache"
    r = run_script(
        "ingest.py",
        kind,
        deployments.fx / name,
        cwd=tmp_path,
        cache=cache,
        deployment="console.typo.example",
    )
    assert r.returncode != 0
    assert not (cache / "console.typo.example").exists()


def test_a_response_with_only_errors_says_so(deployments, tmp_path):
    (tmp_path / "posture.json").write_text(
        json.dumps({"data": None, "errors": [{"message": "Not authorised"}]})
    )
    r = run_in(deployments, "ingest.py", "posture", tmp_path / "posture.json")
    assert r.returncode != 0
    assert "the platform reported errors" in r.stderr
    assert "Not authorised" in r.stderr
    assert "Traceback" not in r.stderr


def mcp_wrapped(src, dest):
    """SRC as the MCP tool returns it: {"query", "variables", "data", "errors": null}."""
    data = json.loads(src.read_text())["data"]
    dest.write_text(
        json.dumps({"query": "{..}", "variables": {}, "data": data, "errors": None})
    )
    return dest


@pytest.mark.parametrize(("kind", "name"), RESPONSES)
def test_the_mcp_tools_wrapper_ingests(deployments, tmp_path, kind, name):
    cache = tmp_path / "cache"
    shutil.copytree(deployments.cache, cache)
    r = run_script(
        "ingest.py",
        kind,
        mcp_wrapped(deployments.fx / name, tmp_path / name),
        cwd=tmp_path,
        cache=cache,
    )
    assert r.returncode == 0, r.stderr


def test_a_response_with_no_data_says_so(deployments, tmp_path):
    (tmp_path / "posture.json").write_text('{"data": null, "errors": null}')
    r = run_in(deployments, "ingest.py", "posture", tmp_path / "posture.json")
    assert r.returncode != 0
    assert "no data object in the response" in r.stderr


def test_ingest_refuses_a_response_naming_no_deployment(deployments, tmp_path):
    d = json.loads((deployments.fx / "posture.json").read_text())
    del d["data"]["platform"]
    (tmp_path / "posture.json").write_text(json.dumps(d))
    r = run_in(deployments, "ingest.py", "posture", tmp_path / "posture.json")
    assert r.returncode != 0
    assert "no platform.urls.console" in r.stderr


def with_console(src, dest, console):
    d = json.loads(src.read_text())
    d["data"]["platform"]["urls"]["console"] = console
    dest.write_text(json.dumps(d))
    return dest


def test_a_posture_with_a_bad_console_is_refused_and_leaves_nothing(
    deployments, tmp_path
):
    cache = tmp_path / "cache"
    shutil.copytree(deployments.cache, cache)
    bad = with_console(
        deployments.fx / "posture.json", tmp_path / "posture.json", "https://bad host/"
    )
    r = run_script("ingest.py", "posture", bad, cwd=tmp_path, cache=cache)
    assert r.returncode != 0
    assert "names no deployment" in r.stderr
    assert "existing cache left untouched" in r.stderr
    assert not list((cache / FIXTURE_DEPLOYMENT).glob("*.tmp"))


def test_an_account_with_a_bad_console_is_skipped_and_the_rest_load(
    deployments, tmp_path
):
    cache = tmp_path / "cache"
    shutil.copytree(deployments.cache, cache)
    bad = with_console(
        deployments.fx / "account_0.json",
        tmp_path / "account_0.json",
        "https://bad host/",
    )
    r = run_script(
        "ingest.py",
        "accounts",
        bad,
        deployments.fx / "account_0.json",
        cwd=tmp_path,
        cache=cache,
    )
    assert r.returncode != 0
    assert "added 1 account(s)" in r.stdout
    assert "names no deployment" in r.stderr


def test_a_posture_copied_into_another_deployment_is_refused(deployments, tmp_path):
    cache = tmp_path / "cache"
    shutil.copytree(deployments.cache, cache)
    (cache / "console.other.example").mkdir()
    shutil.copy(
        cache / ACME / "posture.db", cache / "console.other.example" / "posture.db"
    )
    r = run_script(
        "distill.py",
        "index",
        cwd=tmp_path,
        cache=cache,
        deployment="console.other.example",
    )
    assert r.returncode != 0
    assert f"it was built from {ACME}" in r.stderr


def test_decide_records_deployment(acme_repo):
    assert acme_repo.decided.returncode == 0, acme_repo.decided.stderr
    scope = read_scope(acme_repo.path)
    assert scope.get("deployment") == ACME


def test_decide_with_no_facts_records_just_the_deployment(deployments, tmp_path):
    # the user was asked which deployment: recorded at once, so it isn't asked again
    r = run_in(
        deployments, "scope.py", "decide", "--because", "user chose", cwd=tmp_path
    )
    assert r.returncode == 0, r.stderr
    scope = read_scope(tmp_path)
    assert scope["deployment"] == FIXTURE_DEPLOYMENT
    assert scope["decisions"] == scope["answers"] == scope["accounts"] == []


def test_check_ignores_scope_file_from_other_deployment(deployments, acme_repo):
    # acme's file excludes CIS; against the fixture deployment it says nothing
    r = run_in(deployments, "scope.py", "check", cwd=acme_repo.path)
    assert r.returncode == 1
    report = json.loads(r.stdout)
    assert report["deployment_mismatch"] == f"scope file is for {ACME!r}"
    assert report["excluded_applicable"] == []
    assert report["new_bindings"] == "scope file ignored"


def test_distill_ignores_scope_file_from_other_deployment(deployments, acme_repo):
    r = run_in(deployments, "distill.py", "index", cwd=acme_repo.path)
    assert r.returncode == 0, r.stderr
    assert f"scoped against deployment {ACME!r}" in r.stderr
    assert "aws.s3" in r.stdout


def test_decide_refuses_a_scope_file_from_other_deployment(
    deployments, acme_repo, tmp_path
):
    # a copy, so a regression that does re-point the file can't break the other tests
    repo = shutil.copytree(acme_repo.path, tmp_path / "repo")
    r = run_in(
        deployments,
        "scope.py",
        "decide",
        "--include",
        cid("PCI-DSS AWS"),
        "--because",
        "t",
        cwd=repo,
    )
    assert r.returncode != 0
    assert "scoped against deployment" in r.stderr


def test_a_console_url_written_into_a_scope_file_matches_its_host(
    deployments, tmp_path
):
    write_scope(tmp_path, {"deployment": f"https://{FIXTURE_DEPLOYMENT.upper()}/"})
    r = run_in(deployments, "scope.py", "check", cwd=tmp_path)
    assert json.loads(r.stdout)["deployment_mismatch"] is False


def test_scope_file_without_deployment_adopts_the_one_used(deployments, tmp_path):
    write_scope(tmp_path, '{"version": 5, "decisions": [], "seen_bindings": []}')
    r = run_in(deployments, "scope.py", "check", cwd=tmp_path)
    assert '"deployment_mismatch": false' in r.stdout
    r = run_in(
        deployments,
        "scope.py",
        "decide",
        "--exclude",
        cid("Sandbox Tagging"),
        "--because",
        "t",
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    scope = read_scope(tmp_path)
    assert scope["deployment"] == FIXTURE_DEPLOYMENT


def report_of(cache, cwd, *args):
    """deployments.py's report, run with ARGS."""
    r = run_script("deployments.py", *args, cwd=cwd, cache=cache)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def choose(cache, *consoles, cwd):
    """deployments.py's report, given the servers' CONSOLES."""
    return report_of(cache, cwd, *(("--console", *consoles) if consoles else ()))


def test_deployments_lists_the_caches_and_the_repos_deployment(deployments, acme_repo):
    report = choose(deployments.cache, cwd=acme_repo.path)
    assert {n: c["console"] for n, c in report["cached"].items()} == {
        ACME: f"https://{ACME}/",
        FIXTURE_DEPLOYMENT: f"https://{FIXTURE_DEPLOYMENT}/",
    }
    assert all(c["refreshed_at"] for c in report["cached"].values())
    assert report["scoped_against"] == ACME


def test_the_scope_files_deployment_is_used_from_cache_without_its_server(
    deployments, acme_repo
):
    report = choose(
        deployments.cache, f"https://{FIXTURE_DEPLOYMENT}/", cwd=acme_repo.path
    )
    assert report["use"] == ACME
    assert "no refresh" in report["reason"]


def test_a_scope_files_deployment_with_no_server_or_cache_means_none(
    deployments, tmp_path
):
    # never another deployment instead
    write_scope(tmp_path, '{"deployment": "console.gone.example"}')
    report = choose(deployments.cache, f"https://{FIXTURE_DEPLOYMENT}/", cwd=tmp_path)
    assert report["use"] is None
    assert report["ask"] == []
    assert "console.gone.example" in report["reason"]


def test_the_scope_file_decides_whatever_servers_answer(deployments, acme_repo):
    report = choose(
        deployments.cache,
        f"https://{FIXTURE_DEPLOYMENT}/",
        f"https://{ACME}/",
        "https://console.third.example/",
        cwd=acme_repo.path,
    )
    assert report["use"] == ACME
    assert report["ask"] == []
    assert report["reason"] == "the repo's scope file records it"


def test_an_invalid_scope_file_still_names_the_deployment(deployments, tmp_path):
    # it is ignored for scope, but a broken file must not let the session drift to another tenant
    write_scope(tmp_path, {"version": 1, "deployment": FIXTURE_DEPLOYMENT})
    report = choose(deployments.cache, f"https://{ACME}/", cwd=tmp_path)
    assert report["use"] == FIXTURE_DEPLOYMENT
    r = run_in(deployments, "scope.py", "check", cwd=tmp_path)
    assert json.loads(r.stdout)["scope_file_invalid"]


def test_a_repeated_console_flag_adds_servers(deployments, tmp_path):
    # one flag per server must not leave only the last, which would then decide alone
    r = run_script(
        "deployments.py",
        "--console",
        f"https://{ACME}/",
        "--console",
        f"https://{FIXTURE_DEPLOYMENT}/",
        cwd=tmp_path,
        cache=deployments.cache,
    )
    report = json.loads(r.stdout)
    assert report["use"] is None
    assert sorted(report["servers"]) == [ACME, FIXTURE_DEPLOYMENT]


def test_without_a_scope_file_the_only_server_decides(deployments, tmp_path):
    report = choose(deployments.cache, f"https://{ACME}/", cwd=tmp_path)
    assert report["use"] == ACME


def test_without_a_scope_file_several_servers_mean_asking(deployments, tmp_path):
    report = choose(
        deployments.cache,
        f"https://{ACME}/",
        f"https://{FIXTURE_DEPLOYMENT}/",
        cwd=tmp_path,
    )
    assert report["use"] is None
    assert report["ask"] == [
        {"deployment": ACME, "console": f"https://{ACME}/"},
        {"deployment": FIXTURE_DEPLOYMENT, "console": f"https://{FIXTURE_DEPLOYMENT}/"},
    ]


def test_without_a_server_several_caches_mean_asking(deployments, tmp_path):
    report = choose(deployments.cache, cwd=tmp_path)
    assert report["use"] is None
    assert [a["deployment"] for a in report["ask"]] == [ACME, FIXTURE_DEPLOYMENT]


def test_a_user_who_turned_guardrails_off_is_not_asked_again(deployments, tmp_path):
    # asking every session would nag: their choice stands until they ask for guardrails
    off = report_of(
        deployments.cache, tmp_path, "--disable", "--because", "not for this repo"
    )
    assert off["use"] is None
    assert off["ask"] == []
    report = choose(
        deployments.cache,
        f"https://{ACME}/",
        f"https://{FIXTURE_DEPLOYMENT}/",
        cwd=tmp_path,
    )
    assert report["use"] is None
    assert report["ask"] == []
    assert report["disabled"] == "not for this repo"
    assert "not for this repo" in report["reason"]
    assert "/stacklet:guardrails" in report["reason"]


def test_turning_guardrails_off_wins_over_a_recorded_deployment(deployments, acme_repo):
    report_of(deployments.cache, acme_repo.path, "--disable", "--because", "prototype")
    try:
        report = choose(deployments.cache, f"https://{ACME}/", cwd=acme_repo.path)
        assert report["use"] is None
        assert report["scoped_against"] == ACME
        # the rest of the file is kept
        state = read_scope(acme_repo.path)
        assert state["deployment"] == ACME
        assert state["disabled"]["because"] == "prototype"
        # nor does a decision lift it: only the user's asking does
        r = run_in(
            deployments,
            "scope.py",
            "decide",
            "--because",
            "t",
            deployment=ACME,
            cwd=acme_repo.path,
        )
        assert r.returncode == 0, r.stderr
        assert read_scope(acme_repo.path)["disabled"]
    finally:
        on = report_of(
            deployments.cache,
            acme_repo.path,
            "--enable",
            "--console",
            f"https://{ACME}/",
        )
    assert on["use"] == ACME
    assert on["disabled"] is None
    assert "disabled" not in read_scope(acme_repo.path)


def test_enable_with_nothing_disabled_writes_nothing(deployments, tmp_path):
    report = report_of(
        deployments.cache, tmp_path, "--enable", "--console", f"https://{ACME}/"
    )
    assert report["use"] == ACME
    assert not (tmp_path / ".stacklet").exists()


def test_disable_needs_a_reason(deployments, tmp_path):
    r = run_script("deployments.py", "--disable", cwd=tmp_path, cache=deployments.cache)
    assert r.returncode != 0
    assert "--because" in r.stderr
    assert not (tmp_path / ".stacklet").exists()


def test_a_malformed_disabled_entry_makes_the_file_invalid_but_still_disables(
    deployments, tmp_path
):
    write_scope(tmp_path, {"disabled": True})
    report = choose(deployments.cache, f"https://{ACME}/", cwd=tmp_path)
    # unreadable, but the user's choice to turn guardrails off still shouldn't be overridden
    assert report["use"] is None
    assert report["disabled"] == "no reason given"
    r = run_in(deployments, "scope.py", "check", cwd=tmp_path)
    assert json.loads(r.stdout)["scope_file_invalid"]


def test_without_a_server_the_only_cache_decides(deployments, tmp_path):
    cache = tmp_path / "cache"
    shutil.copytree(deployments.cache / FIXTURE_DEPLOYMENT, cache / FIXTURE_DEPLOYMENT)
    report = choose(cache, cwd=tmp_path)
    assert report["use"] == FIXTURE_DEPLOYMENT
    assert "no refresh" in report["reason"]


def test_deployments_lists_only_directories_with_a_posture(deployments, tmp_path):
    # e.g. left by a refused ingest under a mistyped name
    cache = tmp_path / "cache"
    shutil.copytree(deployments.cache / FIXTURE_DEPLOYMENT, cache / FIXTURE_DEPLOYMENT)
    (cache / "console.typo.example").mkdir()
    (cache / "console.typo.example" / "guardrails.db").touch()
    (cache / "empty.example").mkdir()
    (cache / "Not A Name").mkdir()
    (cache / "Not A Name" / "posture.db").touch()
    # a posture an earlier version of the skill built (another layout): Cache.open refuses it
    shutil.copytree(cache / FIXTURE_DEPLOYMENT, cache / "default")
    con = sqlite3.connect(cache / "default" / "posture.db")
    con.execute("PRAGMA user_version = 2")
    con.close()
    # one copied in from another deployment's directory: refused too
    shutil.copytree(cache / FIXTURE_DEPLOYMENT, cache / "console.copy.example")
    report = choose(cache, cwd=tmp_path)
    assert list(report["cached"]) == [FIXTURE_DEPLOYMENT]
    assert report["use"] == FIXTURE_DEPLOYMENT


def test_a_bad_console_is_ignored_as_no_answer(deployments, tmp_path):
    r = run_script(
        "deployments.py",
        "--console",
        "https://bad host/",
        f"https://{ACME}/",
        cwd=tmp_path,
        cache=deployments.cache,
    )
    assert json.loads(r.stdout)["use"] == ACME
    assert "ignoring --console" in r.stderr


def test_deployments_with_no_cache_and_no_server(tmp_path):
    report = choose(tmp_path / "nothing", cwd=tmp_path)
    assert report["use"] is None
    assert "unavailable" in report["reason"]
    assert report["cached"] == {}
    assert report["scope_file"] is None
    assert not (tmp_path / "nothing").exists()


@pytest.mark.parametrize(
    "bad",
    [
        "../evil",
        "a/b",
        "..",
        "x y",
        "_x",
        "a@b",
        "",
        "https://[bad",
        "C:\\foo",
        "https://a:bad/",
    ],
)
def test_refuses_unsafe_deployment_name(deployments, bad):
    r = run_in(deployments, "posture.py", deployment=bad)
    assert r.returncode != 0
    assert "invalid deployment" in r.stderr


def test_an_unsafe_name_in_a_scope_file_is_only_a_mismatch(deployments, tmp_path):
    # the file never picks the cache, so its value is never used as a path
    write_scope(tmp_path, '{"deployment": "../../etc"}')
    r = run_in(deployments, "scope.py", "check", cwd=tmp_path)
    assert r.returncode == 1
    assert "scope file is for '../../etc'" in r.stdout
    report = choose(deployments.cache, cwd=tmp_path)
    assert report["use"] is None


def test_an_unparseable_recorded_deployment_is_echoed_truncated(deployments, tmp_path):
    write_scope(tmp_path, {"deployment": "x y " + "z" * 1000})
    report = choose(deployments.cache, cwd=tmp_path)
    assert len(report["scoped_against"]) == 100
