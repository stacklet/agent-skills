import json

from conftest import gid
from test_filters import batch
from test_ingest import (
    edit_posture,
    ingest_posture_only,
    posture_binding,
    posture_group,
)


def add_rollout(b):
    """Split the fixture's 40 accounts into two batch groups (25 + 15) and bind one new collection to
    both: no single group covers half, together they cover all."""
    for name, n in (("batch-a", 25), ("batch-b", 15)):
        # a description repeating the name is left out
        b["accountGroups"].append(posture_group(name, batch(name), n, description=name))
    b["policyCollections"].append(
        {
            "id": "id-rollout",
            "uuid": "c-rollout",
            "name": "require-encryption",
            "provider": "AWS",
            "description": None,
            # one of the fixture's policies: an empty collection is ignored by rule, unjudged
            "policyMappings": {"pageInfo": {"total": 1}},
        }
    )
    for name in ("batch-a", "batch-b"):
        b["bindings"].append(posture_binding(name, gid(name), "c-rollout"))
    # a group nothing is bound to: no guardrail, so not in the digest
    b["accountGroups"].append(
        dict(b["accountGroups"][-1], uuid="g-unbound", name="unbound")
    )


def same_names(b):
    """Call add_rollout's batch-b group batch-a too."""
    next(g for g in b["accountGroups"] if g["name"] == "batch-b")["name"] = "batch-a"


def test_the_digest_shows_a_rollout_over_several_groups_as_one_pattern(
    run, fx, tmp_path
):
    edit_posture(fx, add_rollout)
    ingest_posture_only(run, fx, tmp_path)
    r = run("posture.py", "--digest")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "## AWS: 40 accounts" in out
    assert (
        "- batch-a: 25 accounts (62%); tag:RolloutWave equals batch-a AND NOT (tag:Ignore exists)\n"
        in out
    )
    assert (
        "- bound to 2 groups (batch-a, batch-b): combined 40 accounts (100%)\n"
        "  - require-encryption: 1 policy\n" in out
    )
    assert "unbound" not in out
    assert (
        "- aws-sandbox-jdoe: 1 account (2%); legacy filter: name = jdoe-sandbox\n"
        in out
    )
    assert "described as: Accounts running the payments service" in out


def test_non_ascii_names_print_on_an_ascii_console(run, fx, tmp_path):
    # e.g. a Windows cp1252 pipe: the scripts write UTF-8 whatever the console says
    def rename(b):
        b["policyCollections"][0]["name"] = "Café → standards"

    edit_posture(fx, rename)
    ingest_posture_only(run, fx, tmp_path)
    r = run("posture.py", PYTHONIOENCODING="ascii")
    assert r.returncode == 0, r.stderr
    assert "Café → standards" in r.stdout


def test_groups_sharing_a_name_are_told_apart_by_uuid(run, fx, tmp_path):
    # a question names the groups it asks about: each must have a name of its own
    edit_posture(fx, add_rollout)
    edit_posture(fx, same_names)
    ingest_posture_only(run, fx, tmp_path)
    out = run("posture.py", "--digest").stdout
    a, b = sorted(f"batch-a [{gid(n)}]" for n in ("batch-a", "batch-b"))
    assert f"- {a}: " in out
    assert f"- {b}: " in out
    assert f"- bound to 2 groups ({a}, {b}): combined 40 accounts" in out


def test_the_digest_lists_groups_under_what_holds_them(run, fx, tmp_path):
    def ous(b):
        org = {"single": {"name": "org", "operator": "equals", "values": ["o-acme"]}}
        for name, f, n in (
            ("acme", json.dumps(org), 30),
            ("acme-apps", "org:o-acme path:/Apps/", 20),
            ("acme-apps-pci", "org:o-acme tag:pci~=yes path:/Apps/", 4),
        ):
            b["accountGroups"].append(posture_group(name, f, n, description=name))
            b["bindings"].append(
                posture_binding(name, gid(name), b["policyCollections"][0]["uuid"])
            )

    edit_posture(fx, ous)
    ingest_posture_only(run, fx, tmp_path)
    out = run("posture.py", "--digest").stdout
    assert (
        "- acme: 30 accounts (75%); org equals o-acme\n"
        "  - acme-apps: 20 accounts (50%); org equals o-acme AND path starts with /Apps/ "
        "(legacy filter)\n"
        "    - acme-apps-pci: 4 accounts (10%); org equals o-acme AND tag:pci~=yes AND path "
        "starts with /Apps/ (legacy filter)\n" in out
    )


def test_a_binding_to_a_group_the_posture_lacks_is_left_out(run, fx, tmp_path):
    def stray(b):
        b["bindings"][0]["accountGroup"]["uuid"] = "nope"

    edit_posture(fx, stray)
    ingest_posture_only(run, fx, tmp_path)
    r = run("posture.py", "--digest")
    assert r.returncode == 0, r.stderr
    assert "nope" not in r.stdout
