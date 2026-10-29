"""The estate fixture (fixture/make_estate.py): it builds, ingests, and keeps the shapes the
skill's evals (22-28) rely on."""

import json
import subprocess
import sys

import pytest

from conftest import HERE, ingest_dir


ESTATE = "console.estate.example"


@pytest.fixture(scope="module")
def estate(tmp_path_factory):
    d = tmp_path_factory.mktemp("estate")
    subprocess.run(
        [sys.executable, str(HERE / "fixture" / "make_estate.py"), str(d)], check=True
    )
    return d


@pytest.fixture
def estate_run(run):
    return lambda *args: run(*args, deployment=ESTATE)


def test_it_ingests_with_everything_bound_loaded(estate, estate_run):
    r = ingest_dir(estate_run, estate)
    assert r.returncode == 0, r.stderr
    accounts = sorted(estate.glob("account_*.json"))
    assert estate_run("ingest.py", "accounts", *accounts).returncode == 0
    # the empty collection has nothing to load
    assert json.loads(estate_run("posture.py", "--pending").stdout) == []


def test_the_digest_shows_the_shapes(estate, estate_run):
    assert ingest_dir(estate_run, estate).returncode == 0
    out = estate_run("posture.py").stdout
    # waves no single one of which is a quarter of the accounts, together nearly all
    assert "combined 157 accounts (98%)" in out
    # with the canary account (a wave of its own) and the two paused ones: every account, no more
    assert "combined 160 accounts (100%)" in out
    assert "(101%)" not in out
    assert "mrd-aws-prod-gamma-wave-standard: 31 accounts (19%)" in out
    # the opt-out tag and the named exemption, in the filters
    assert (
        "NOT (tag:GuardrailPause exists) AND NOT (name equals billing-sandbox)" in out
    )
    # one GCP org of two, a trial remediation group that deletes, an empty collection
    assert "- bound to All atelier.example accounts: combined 31 accounts (22%)" in out
    assert "Trial Remediation: 1 account (1%); no filter (members added by hand)" in out
    assert "removes resources that miss the owner tagging rules" in out
    assert "mrd-prod-aws-identity-create-notify: 0 policies" in out
    # unbound groups and collections stay out
    assert "acct-000-group" not in out
    assert "mrd-aws-draft-collection" not in out


def test_the_paused_account_s_tag_reaches_check(estate, estate_run):
    assert ingest_dir(estate_run, estate).returncode == 0
    accounts = sorted(estate.glob("account_*.json"))
    assert estate_run("ingest.py", "accounts", *accounts).returncode == 0
    r = estate_run(
        "scope.py", "decide", "--account", "AWS:345678901234", "--because", "t"
    )
    assert r.returncode == 0, r.stderr
    report = json.loads(estate_run("scope.py", "check").stdout)
    acct = report["accounts"]["AWS:345678901234"]
    assert acct["tags"] == ["RolloutWave=ProdAlphaWave", "GuardrailPause=until-2027-01"]
    # in no wave group: only the all-accounts and org-wide collections reach it
    assert acct["collections"] == 5


def test_what_the_opt_out_switches_off_is_bound_to_wave_groups_only():
    # eval 24's paused account answers the opt-out ("not into" the wave groups), which must then
    # switch off every collection on what it writes (an ElastiCache group, so its engine,
    # encryption and tags): a binding to a group without the opt-out would keep one in force
    sys.path.insert(0, str(HERE / "fixture"))
    import make_estate

    waves = {g[0] for g in make_estate.wave_groups()}
    cache = {"aws.elasticache-group", "aws.cache-cluster"}
    seen = 0
    for name, _, key, groups, _, _ in make_estate.COLLECTIONS:
        types = {x["resource_type"] for x in make_estate.P[key]} if key else set()
        if types & cache and set(groups) & waves:
            assert set(groups) <= waves, name
            seen += 1
    assert seen == 3  # the engine, encryption and tagging collections
