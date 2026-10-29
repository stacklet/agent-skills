import hashlib
import json
import shutil
import sqlite3

import ingest as ingest_py
import posture as posture_py
import pytest

from _cache import alias_of
from conftest import (
    FIXTURE_DEPLOYMENT,
    cid,
    edit_collections,
    edit_policies,
    edit_response,
    gid,
    hold_back,
    org_filter,
    policies_of,
)


def digest(run):
    return run("posture.py").stdout


@pytest.fixture
def content_query(open_cache):
    """posture.content_query's queries for UUIDS, as scope.py query prints them ("" for none)."""
    return lambda *uuids: posture_py.format_queries(
        posture_py.content_query(open_cache(), list(uuids))
    )


def pending(run):
    return json.loads(run("posture.py", "--pending").stdout)


def in_scope(run):
    """What applies with no assessment (the tests' inference command fails): the names."""
    out = json.loads(run("scope.py", "check").stdout)["in_scope"]
    return sorted(c["name"] for c in out.values())


def write_posture(d, p):
    (d / "posture.json").write_text(json.dumps(p))


def read_posture(d):
    return json.loads((d / "posture.json").read_text())


def edit_posture(d, fn):
    p = read_posture(d)
    fn(p["data"]["bindingPosture"])
    write_posture(d, p)


def posture_only(src, dest):
    """A directory holding just src's posture.json (so no collection is loaded with it)."""
    dest.mkdir()
    shutil.copy(src / "posture.json", dest)
    return dest


def ingest_posture_only(run, src, tmp_path):
    """Ingest src's posture with none of its collections (the digest needs no policy content), from a
    copy in tmp_path."""
    r = run("ingest.py", "posture", posture_only(src, tmp_path / "p") / "posture.json")
    assert r.returncode == 0, r.stderr
    return tmp_path / "p"


def break_acme_policy(d):
    """Give one of Acme Org Standards' policies (the last collection) content that can't be stored."""
    acme = cid("Acme Org Standards")
    edit_collections(d, lambda cs: policies_of(cs[acme])[-1].update(source=None))


def posture_group(name, dynamic_filter, n, uuid=None, description=None):
    """An account group as posture.graphql returns it, holding N accounts."""
    return {
        "uuid": uuid or gid(name),
        "name": name,
        "provider": "AWS",
        "description": description,
        "dynamicFilter": dynamic_filter,
        "regions": [],
        "accountMappings": {"pageInfo": {"total": n}},
    }


def posture_binding(name, group_uuid, collection_uuid):
    """A deployed binding as posture.graphql returns it."""
    return {
        "uuid": f"b-{name}",
        "name": name,
        "description": None,
        "lastDeployed": "2026-10-01T00:00:00Z",
        "accountGroup": {"uuid": group_uuid},
        "policyCollection": {"uuid": collection_uuid},
    }


def add_empty_collection(d, in_response=False):
    """A collection the platform reports as empty (policy_count 0), bound to the all-accounts group;
    IN_RESPONSE, also in d's collections.json, as a fetch of it would return it."""
    p = read_posture(d)
    b = p["data"]["bindingPosture"]
    grp = next(g for g in b["accountGroups"] if g["dynamicFilter"] == "")
    b["policyCollections"].append(
        {
            "uuid": "uuid-empty",
            "name": "Empty Collection",
            "provider": "AWS",
            "description": None,
            "policyMappings": {"pageInfo": {"total": 0}},
        }
    )
    b["bindings"].append(posture_binding("empty", grp["uuid"], "uuid-empty"))
    write_posture(d, p)
    if in_response:
        r = json.loads((d / "collections.json").read_text())
        r["data"][alias_of("uuid-empty")] = {
            "uuid": "uuid-empty",
            "policyMappings": {
                "pageInfo": {"total": 0, "hasNextPage": False},
                "edges": [],
            },
        }
        (d / "collections.json").write_text(json.dumps(r))


def test_severity_ranking_is_case_insensitive(ingested, run):
    r = run("distill.py", "index")
    sqs = next(l for l in r.stdout.splitlines() if l.startswith("aws.sqs"))
    # one HIGH among MEDIUMs; fixture severities are upper-case
    assert "up to HIGH" in sqs


def test_failed_ingest_keeps_old_cache_and_leaves_no_temp_files(ingest, cache, fx):
    assert ingest().returncode == 0
    db = cache / FIXTURE_DEPLOYMENT / "posture.db"
    before = db.read_bytes()
    (fx / "posture.json").write_text('{"data": {}}')
    r = ingest(fx)
    assert r.returncode != 0
    assert "existing cache left untouched" in r.stderr
    assert db.read_bytes() == before
    assert sorted(p.name for p in db.parent.iterdir()) == [
        "guardrails.db",
        "posture.db",
    ]


def test_duplicate_collection_names_are_fine(run, ingest, fx):
    # collections are identified by uuid, so the same name under two providers is no clash
    p = read_posture(fx)
    cols = p["data"]["bindingPosture"]["policyCollections"]
    cols.append(dict(cols[0], uuid="dup", provider="GCP"))
    write_posture(fx, p)
    assert ingest(fx).returncode == 0
    assert "  - CIS AWS Baseline: 6 policies" in digest(run)


def test_the_total_is_the_all_accounts_group_else_the_active_count(run, ingest, fx):
    # the all-accounts group (filter "") is the provider's total, whatever the fetched active count says
    p = read_posture(fx)
    p["data"]["active_AWS"] = {"pageInfo": {"total": 400}}
    write_posture(fx, p)
    assert ingest(fx).returncode == 0
    assert "## AWS: 40 accounts" in digest(run)
    # no such group: fall back to the active count
    p["data"]["bindingPosture"]["accountGroups"][0]["dynamicFilter"] = "tag:all = yes"
    write_posture(fx, p)
    assert ingest(fx).returncode == 0
    out = digest(run)
    assert "## AWS: 400 accounts" in out
    assert "- aws-payments: 3 accounts (1%)" in out
    # a null total (no accounts, or an unknown slug) and no total at all give no shares, and say so
    p["data"]["active_AWS"] = {"pageInfo": {"total": None}}
    write_posture(fx, p)
    assert ingest(fx).returncode == 0
    out = digest(run)
    assert "## AWS: ? accounts" in out
    assert "- aws-payments: 3 accounts (?)" in out
    # a mistyped slug must not pass silently
    assert "count for AWS is null" in ingest(fx).stderr
    del p["data"]["active_AWS"]
    write_posture(fx, p)
    r = ingest(fx)
    assert r.returncode == 0
    assert "no account total for AWS" in r.stderr


def test_an_active_count_comes_as_its_own_response(run, fx):
    # active_accounts.graphql's alias names the provider, so its response is ingested as it is
    edit_posture(
        fx, lambda b: b["accountGroups"][0].update(dynamicFilter="tag:all = yes")
    )
    console = read_posture(fx)["data"]["platform"]
    active = fx / "active_0.json"
    active.write_text(
        json.dumps(
            {"data": {"platform": console, "active_AWS": {"pageInfo": {"total": 400}}}}
        )
    )
    r = run("ingest.py", "posture", fx / "posture.json", active)
    assert r.returncode == 0, r.stderr
    assert "no account total" not in r.stderr
    assert "## AWS: 400 accounts" in digest(run)
    # not this deployment's, or with no count: refused, the cache left as it was
    other = {"urls": {"console": "https://console.other.example/"}}
    for data in (
        {"platform": other, "active_AWS": {"pageInfo": {"total": 9}}},
        {"platform": console, "accounts": {"pageInfo": {"total": 9}}},
    ):
        active.write_text(json.dumps({"data": data}))
        r = run("ingest.py", "posture", fx / "posture.json", active)
        assert r.returncode != 0
        assert "existing cache left untouched" in r.stderr
    assert "## AWS: 400 accounts" in digest(run)


def test_only_an_empty_filter_means_all_accounts(run, ingest, fx):
    # a static group (null filter, as the platform reports them) is no all-accounts group, so
    # without an assessment what is bound to it doesn't apply
    edit_posture(fx, lambda b: b["accountGroups"][1].update(dynamicFilter=None))
    assert ingest(fx).returncode == 0
    assert in_scope(run) == ["Acme Org Standards", "CIS AWS Baseline"]
    assert "no filter (members added by hand)" in digest(run)


def test_an_all_accounts_group_applies_even_when_empty(run, ingest, fx):
    # it matches every account, including the first one this code may create
    p = read_posture(fx)
    p["data"]["active_AWS"] = {"pageInfo": {"total": 40}}
    p["data"]["bindingPosture"]["accountGroups"][0]["accountMappings"]["pageInfo"][
        "total"
    ] = 0
    write_posture(fx, p)
    r = ingest(fx)
    assert "no account total" not in r.stderr
    assert "CIS AWS Baseline" in in_scope(run)


def test_a_collection_not_fetched_is_pending_and_flagged_not_a_silent_gap(
    run, ingest, fx
):
    # CIS AWS Baseline: bound to all accounts, so in scope (an out-of-scope one would rightly not be
    # flagged)
    cis = cid("CIS AWS Baseline")
    hold_back(fx, lambda c: c["uuid"] == cis)
    assert ingest(fx).returncode == 0
    pend = pending(run)
    assert [(x["name"], x["why"]) for x in pend] == [
        ("CIS AWS Baseline", "not fetched")
    ]
    r = run("distill.py", "index")
    assert "not loaded" in r.stderr
    assert "CIS AWS Baseline" in r.stderr


def test_collections_load_separately_and_a_posture_refresh_keeps_their_content(
    run, ingest, collections, fx, tmp_path
):
    # PCI-DSS AWS fetched in a response of its own, later
    up = hold_back(fx, lambda c: c["uuid"] == cid("PCI-DSS AWS"))
    assert ingest(fx).returncode == 0
    assert [x["name"] for x in pending(run)] == ["PCI-DSS AWS"]
    r = collections(up)
    assert r.returncode == 0, r.stderr
    assert "loaded 1 collection(s): PCI-DSS AWS" in r.stdout
    assert pending(run) == []
    # a posture refresh: what was loaded is still read, but every membership is due again
    index = run("distill.py", "index")
    assert ingest(posture_only(fx, tmp_path / "po")).returncode == 0
    due = pending(run)
    assert {x["why"] for x in due} == {"fetched under an older posture"}
    again = run("distill.py", "index")
    assert again.stdout == index.stdout
    assert "read from a membership older than the posture" in again.stderr
    assert collections(fx).returncode == 0
    assert collections(up).returncode == 0
    assert pending(run) == []


def test_a_policy_that_cant_be_stored_leaves_its_collection_pending(
    run, ingest, cache, fx
):
    break_acme_policy(fx)
    r = ingest(fx)
    assert r.returncode != 0
    assert "no source" in r.stderr
    # not said to have loaded: its guardrails would be short
    assert "Acme Org Standards" not in r.stdout
    # everything else, including the rest of that collection, loaded
    assert [(x["name"], x["why"]) for x in pending(run)] == [
        ("Acme Org Standards", "lacks content")
    ]
    con = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "guardrails.db")
    acme = con.execute(
        "SELECT COUNT(*) FROM policy WHERE name LIKE 'acme:%'"
    ).fetchone()[0]
    members_n = con.execute(
        "SELECT COUNT(*) FROM collection_policy WHERE collection_uuid = ?",
        (cid("Acme Org Standards"),),
    ).fetchone()[0]
    con.close()
    assert acme == members_n - 1


def test_a_never_deployed_binding_is_left_out(run, ingest, fx):
    p = read_posture(fx)
    # PCI-DSS AWS -> aws-payments
    p["data"]["bindingPosture"]["bindings"][1]["lastDeployed"] = None
    (fx / "collections.json").unlink()
    write_posture(fx, p)
    assert ingest(fx).returncode == 0
    assert "PCI-DSS AWS" not in digest(run)
    assert "PCI-DSS AWS" not in [x["name"] for x in pending(run)]
    assert len([x["name"] for x in pending(run)]) == 3


def test_replaced_versions_are_kept_but_only_bound_ones_are_guardrails(
    run, ingest, fx, tmp_path
):
    assert ingest(fx).returncode == 0
    index = run("distill.py", "index").stdout
    # a posture refresh first: a version bump at the same count is exactly what a count can't see,
    # so every membership is fetched again after one
    assert ingest(posture_only(fx, tmp_path / "po")).returncode == 0

    # the auto-update case: same policy, next version, same count
    def bump(p):
        if p["name"] == "cis:s3-block-public-access":
            p.update(
                version=2,
                source=json.dumps({**json.loads(p["source"]), "bumped": True}),
            )

    edit_policies(fx, bump)
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode == 0, r.stderr
    assert pending(run) == []
    # each version by its ref
    assert (
        "bumped" in run("distill.py", "source", "cis:s3-block-public-access@2").stdout
    )
    # what an older distillation cites still resolves
    r = run("distill.py", "source", "cis:s3-block-public-access@1")
    assert r.returncode == 0, r.stderr
    assert "bumped" not in r.stdout
    # not counted twice
    assert run("distill.py", "index").stdout == index


def test_action_names_are_derived_when_read(run, ingest, fx):
    def add_actions(p):
        src = json.loads(p["source"])
        if p["name"] == "cis:s3-block-public-access":
            src["actions"] = ["delete"]
        elif p["name"] == "cis:s3-encryption-at-rest":
            src["actions"] = [{"type": "mark-for-op", "op": "delete", "days": 4}]
        else:
            return
        p["source"] = json.dumps(src)

    edit_policies(fx, add_actions)
    assert ingest(fx).returncode == 0
    assert (
        "aws.s3: 4 guardrails, up to HIGH; actions: delete (1), mark-for-op:delete (1)"
        in run("distill.py", "index").stdout
    )


def test_a_lost_guardrails_db_reads_as_nothing_loaded(run, ingest, cache, fx):
    assert ingest().returncode == 0
    (cache / FIXTURE_DEPLOYMENT / "guardrails.db").unlink()
    # the content is gone, so everything is pending, and said to be
    assert len(pending(run)) == 4
    missing = "not loaded, so their guardrails are missing"
    assert missing in run("distill.py", "index").stderr
    # the sequence that once read as "no guardrails apply": content lost, then a refresh that fails
    break_acme_policy(fx)
    assert ingest(fx).returncode != 0
    assert missing in run("distill.py", "index").stderr
    assert ingest().returncode == 0
    assert pending(run) == []


def test_a_file_that_is_not_a_response_is_said_and_the_others_still_load(
    run, ingest, fx
):
    (fx / "collections_9.json").write_text("[1, 2]", encoding="utf-8")
    r = ingest(fx)
    assert r.returncode != 0
    assert "collections_9.json: TypeError: not a GraphQL response" in r.stderr
    assert pending(run) == []


def test_an_old_posture_is_noted_by_index(ingested, run, cache):
    assert "posture cache is" not in run("distill.py", "index").stderr
    con = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "posture.db")
    con.execute(
        "UPDATE meta SET value = '2020-01-01T00:00:00Z' WHERE key = 'refreshed_at'"
    )
    con.commit()
    con.close()
    r = run("distill.py", "index")
    assert r.returncode == 0
    assert "day(s) old (refreshed 2020-01-01T00:00:00Z)" in r.stderr


def test_descriptions_are_offered_for_judging_scope(ingested, run):
    out = digest(run)
    assert (
        "  - PCI-DSS AWS: 3 policies; described as: Controls for systems that store or process "
        "card data; binding to aws-payments: PCI scope agreed with the payments team\n"
    ) in out
    assert (
        "- aws-payments: 3 accounts (8%); legacy filter: tag:team = payments; described as: "
        "Accounts running the payments service\n"
    ) in out
    # blank descriptions are left out
    assert "  - Sandbox Tagging: 4 policies\n" in out


def test_a_binding_description_naming_its_group_is_left_out(run, ingest, fx):
    # generated ones only describe the group ("Compliance policies for <group> accounts")
    edit_posture(
        fx,
        lambda b: b["bindings"][1].update(
            description="Compliance policies for aws-payments accounts"
        ),
    )
    assert ingest(fx).returncode == 0
    assert "binding to" not in digest(run)


def test_pending_lists_bound_collections_only(run, ingest, fx):
    # a collection nothing binds is never fetched, so it is no work to do
    def unbound(b):
        b["policyCollections"].append(
            dict(b["policyCollections"][0], uuid="uuid-x", name="Unbound")
        )

    edit_posture(fx, unbound)
    (fx / "collections.json").unlink()
    assert ingest(fx).returncode == 0
    assert "Unbound" not in {x["name"] for x in pending(run)}
    assert len(pending(run)) == 4


@pytest.mark.parametrize("how", ["short", "next page"])
def test_a_truncated_membership_is_refused_and_the_rest_load(
    run, ingest, cache, fx, how
):
    assert ingest().returncode == 0
    cis = cid("CIS AWS Baseline")

    def truncate(cs):
        m = cs[cis]["policyMappings"]
        if how == "short":
            m["edges"].pop()
        else:  # every edge it counts, but the platform says there are more
            m["pageInfo"]["hasNextPage"] = True
        # a version bump each: which memberships were stored shows
        for c in cs.values():
            policies_of(c)[0]["version"] = 9

    edit_collections(fx, truncate)
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode != 0
    assert "raise `first:`" in r.stderr
    con = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "guardrails.db")
    bumped = dict(
        con.execute(
            "SELECT collection_uuid, MAX(policy_version) FROM collection_policy"
            " GROUP BY collection_uuid"
        )
    )
    con.close()
    # the truncated one kept its last membership; the others were replaced
    assert bumped.pop(cis) == 1
    assert set(bumped.values()) == {9}
    assert pending(run) == []


def test_a_content_query_asks_for_what_is_due(
    run, pristine_fx, tmp_path, content_query
):
    ingest_posture_only(run, pristine_fx, tmp_path)
    bound = [x["uuid"] for x in pending(run)]
    q = content_query(*bound)
    assert all(f'"{u}"' in q for u in bound)
    assert q.startswith("# query 1 of 1\n{\n  platform { urls { console } }")
    assert "fragment Collection on PolicyCollection" in q
    with pytest.raises(SystemExit, match="no-such-uuid"):
        content_query("no-such-uuid")
    r = run("ingest.py", "collections", pristine_fx / "collections.json")
    assert r.returncode == 0
    assert content_query(*bound) == ""


def test_a_small_batch_is_filled_out_so_its_result_comes_back_as_a_file(
    run, pristine_fx, tmp_path, content_query
):
    # the fixture's collections are small: asking for one brings the others along
    ingest_posture_only(run, pristine_fx, tmp_path)
    one = pending(run)[-1]["uuid"]
    q = content_query(one)
    assert f'{alias_of(one)}: policyCollection(uuid: "{one}")' in q
    assert all(f'"{x["uuid"]}"' in q for x in pending(run))


@pytest.mark.parametrize(
    ("due", "spare", "want"),
    [
        # big ones alone, small ones together
        (
            [("a", 500), ("b", 200), ("c", 90), ("d", 60)],
            [],
            [["a"], ["b", "c"], ["d"]],
        ),
        # one over the batch size beside small ones: alone, the small ones together
        ([("a", 2000), ("b", 10), ("c", 50)], [], [["a"], ["b", "c"]]),
        # first fit: a small one goes into the first batch with room
        ([("a", 280), ("b", 250), ("c", 10)], [], [["a", "c"], ["b"]]),
        # a last batch too small for a file (what fitted nowhere else) is filled out from the spare
        ([("a", 300), ("b", 290), ("c", 30)], [("x", 20)], [["a"], ["b"], ["c", "x"]]),
        ([("a", 3000), ("b", 50)], [], [["a"], ["b"]]),
        # one small batch is filled out from the spare, up to what's needed
        ([("a", 5)], [("x", 30), ("y", 20), ("z", 10)], [["a", "x", "y"]]),
        # but never past a batch's size
        ([("a", 5)], [("x", 400), ("y", 50)], [["a", "y"]]),
        # nothing due, nothing asked for, whatever the spare
        ([], [("x", 30)], []),
    ],
)
def test_batches(due, spare, want, monkeypatch):
    # the packing, not the sizes: those are measurements, and change
    monkeypatch.setattr(posture_py, "BATCH", 300)
    monkeypatch.setattr(posture_py, "FILE_MIN", 40)

    def rows(xs):
        return [{"uuid": u, "policies": n} for u, n in xs]

    assert posture_py.batches(rows(due), rows(spare)) == want


def test_a_collection_the_platform_lacks_is_named(run, fx, tmp_path):
    edit_response(
        fx, lambda r: r["data"].update({alias_of(cid("CIS AWS Baseline")): None})
    )
    ingest_posture_only(run, fx, tmp_path)
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode != 0
    # by name, though nothing in the response names it
    assert "CIS AWS Baseline: no such collection" in r.stderr
    assert "loaded 3 collection(s)" in r.stdout


def test_a_collection_now_empty_is_due_a_fetch_like_any_other(
    run, ingest, fx, content_query
):
    assert ingest(fx).returncode == 0
    before = run("distill.py", "index").stdout
    cis = cid("CIS AWS Baseline")

    def empty(b):
        c = next(c for c in b["policyCollections"] if c["uuid"] == cis)
        c["policyMappings"]["pageInfo"]["total"] = 0

    edit_posture(fx, empty)
    assert run("ingest.py", "posture", fx / "posture.json").returncode == 0
    # its older membership is still read until fetched again, as for any collection
    assert ("CIS AWS Baseline", "fetched under an older posture") in {
        (x["name"], x["why"]) for x in pending(run)
    }
    assert run("distill.py", "index").stdout == before
    # and it is asked for again, though the posture counts no policies in it (or it would stay due,
    # its old guardrails read, forever)
    assert f'"{cis}"' in content_query(cis)

    def clear(cs):
        m = cs[cis]["policyMappings"]
        m["edges"], m["pageInfo"]["total"] = [], 0

    edit_collections(fx, clear)
    assert run("ingest.py", "collections", fx / "collections.json").returncode == 0
    assert pending(run) == []
    # its policies no longer count
    assert run("distill.py", "index").stdout != before


def test_an_error_about_one_collection_skips_only_that_one(run, ingest, fx, tmp_path):
    ingest_posture_only(run, fx, tmp_path)
    cis = alias_of(cid("CIS AWS Baseline"))
    edit_response(
        fx,
        lambda r: r.update(
            data=dict(r["data"], **{cis: None}),
            errors=[{"message": "resolver failed", "path": [cis, "policyMappings"]}],
        ),
    )
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode != 0
    assert "CIS AWS Baseline: the platform failed: resolver failed" in r.stderr
    assert "loaded 3 collection(s)" in r.stdout
    # the same when the platform leaves its alias out of the data altogether
    edit_response(fx, lambda r: r["data"].pop(cis))
    r = run("ingest.py", "collections", fx / "collections.json")
    assert "CIS AWS Baseline: the platform failed: resolver failed" in r.stderr
    assert "loaded 3 collection(s)" in r.stdout


def test_an_error_about_no_one_collection_refuses_the_response(run, fx, tmp_path):
    ingest_posture_only(run, fx, tmp_path)
    edit_response(fx, lambda r: r.update(errors=[{"message": "rate limited"}]))
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode != 0
    assert "the platform reported errors" in r.stderr
    assert "loaded 0 collection(s)" in r.stdout


def test_a_membership_counts_under_the_posture_it_was_fetched_under(
    run, ingest, cache, fx, tmp_path, content_query
):
    # another session may refresh the posture between the query and the ingest
    ingest_posture_only(run, fx, tmp_path)
    con = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "posture.db")
    now = con.execute("SELECT value FROM meta WHERE key = 'refresh_id'").fetchone()[0]
    con.close()
    q = content_query(cid("CIS AWS Baseline"))
    assert f"posture_{now}: __typename" in q
    edit_response(fx, lambda r: r["data"].update(posture_older="Query"))
    assert run("ingest.py", "collections", fx / "collections.json").returncode == 0
    # loaded, but not checked under the current posture
    assert {x["why"] for x in pending(run)} == {"fetched under an older posture"}
    edit_response(
        fx,
        lambda r: r["data"].update({f"posture_{now}": r["data"].pop("posture_older")}),
    )
    assert run("ingest.py", "collections", fx / "collections.json").returncode == 0
    assert pending(run) == []


def refresh_id(cache):
    con = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "posture.db")
    (r,) = con.execute("SELECT value FROM meta WHERE key = 'refresh_id'").fetchone()
    con.close()
    return r


def test_an_older_postures_membership_never_replaces_the_current_ones(
    run, ingest, cache, fx, tmp_path
):
    # two sessions: one fetched under the posture before, and ingests after the other's fetch
    ingest_posture_only(run, fx, tmp_path)
    old = refresh_id(cache)
    stale = tmp_path / "stale"
    stale.mkdir()
    shutil.copy(fx / "collections.json", stale)
    edit_response(stale, lambda r: r["data"].update({f"posture_{old}": "Query"}))

    def drop_one(cs):
        # what each held then: a policy fewer
        for c in cs.values():
            m = c["policyMappings"]
            m["edges"].pop()
            m["pageInfo"]["total"] -= 1

    edit_collections(stale, drop_one)
    assert run("ingest.py", "posture", fx / "posture.json").returncode == 0
    assert run("ingest.py", "collections", fx / "collections.json").returncode == 0
    before = run("distill.py", "index").stdout
    r = run("ingest.py", "collections", stale / "collections.json")
    assert r.returncode != 0
    assert "CIS AWS Baseline: ValueError: fetched under an older posture" in r.stderr
    assert "loaded 0 collection(s)" in r.stdout
    assert pending(run) == []
    assert run("distill.py", "index").stdout == before


def test_a_response_not_saying_its_posture_is_refused(run, fx, tmp_path):
    ingest_posture_only(run, fx, tmp_path)
    r = run("ingest.py", "collections", fx / "collections.json", stamp=False)
    assert r.returncode != 0
    assert "no posture_<id> alias" in r.stderr
    assert len(pending(run)) == 4


def test_the_platforms_errors_are_given_when_there_is_no_data(run, fx, tmp_path):
    ingest_posture_only(run, fx, tmp_path)
    edit_response(
        fx, lambda r: r.update(data=None, errors=[{"message": "query too complex"}])
    )
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode != 0
    assert "query too complex" in r.stderr


def test_a_refresh_leaves_collections_loaded_but_unchecked(run, ingest, fx, tmp_path):
    assert ingest(fx).returncode == 0
    entry = json.loads(run("scope.py", "check").stdout)["in_scope"]
    assert {(e["loaded"], e["checked"]) for e in entry.values()} == {(True, True)}
    assert ingest(posture_only(fx, tmp_path / "po")).returncode == 0
    entry = json.loads(run("scope.py", "check").stdout)["in_scope"]
    assert {(e["loaded"], e["checked"]) for e in entry.values()} == {(True, False)}


def test_a_policy_twice_in_one_collection_is_refused(run, fx, tmp_path):
    ingest_posture_only(run, fx, tmp_path)
    cis = cid("CIS AWS Baseline")

    def twice(cs):
        m = cs[cis]["policyMappings"]
        m["edges"].append(m["edges"][0])
        m["pageInfo"]["total"] += 1

    edit_collections(fx, twice)
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode != 0
    assert "a policy appears twice" in r.stderr
    assert "CIS AWS Baseline" in {x["name"] for x in pending(run)}


def test_very_large_policy_field_is_accepted(ingest, fx):
    edit_policies(
        fx,
        lambda p: (
            p.update(source=json.dumps({"description": "x" * 300000}))
            if p["name"] == "cis:s3-block-public-access"
            else None
        ),
    )
    assert ingest(fx).returncode == 0


def test_a_repeated_response_is_harmless(run, ingest, fx):
    shutil.copy(fx / "collections.json", fx / "collections_9.json")
    assert ingest(fx).returncode == 0
    assert pending(run) == []


@pytest.mark.parametrize("in_response", [False, True])
def test_empty_collection_is_not_an_error(run, ingest, fx, in_response):
    add_empty_collection(fx, in_response)
    r = ingest(fx)
    assert r.returncode == 0, r.stderr
    assert "warning" not in r.stderr
    # bound to all accounts: in scope even without an assessment
    assert "uuid-empty" in json.loads(run("scope.py", "check").stdout)["in_scope"]
    assert run("distill.py", "index").returncode == 0
    r = run("scope.py", "decide", "--exclude", "uuid-empty", "--because", "t")
    assert r.returncode == 0


def test_every_collection_empty_still_works(run, ingest, fx):
    p = read_posture(fx)
    for c in p["data"]["bindingPosture"]["policyCollections"]:
        c["policyMappings"]["pageInfo"]["total"] = 0
    write_posture(fx, p)
    (fx / "collections.json").unlink()
    assert ingest(fx).returncode == 0
    r = run("distill.py", "index")
    assert r.returncode == 0
    assert "Traceback" not in r.stderr


def test_missing_cache_gives_a_message_and_a_refresh_still_works(run, ingest, cache):
    r = run("distill.py", "index")
    assert r.returncode != 0
    assert "no posture cache" in r.stderr
    (cache / FIXTURE_DEPLOYMENT).mkdir()
    # may leave an empty posture.db behind
    r = run("scope.py", "check")
    assert r.returncode != 0
    assert "no posture cache" in r.stderr
    # which the first refresh simply replaces
    assert ingest().returncode == 0
    assert run("distill.py", "index").returncode == 0


def test_a_posture_of_another_layout_reads_as_no_cache_and_a_refresh_fixes_it(
    run, ingest, cache, pristine_fx
):
    # a posture.db from before the layout stamp (or another layout): queries would hit missing
    # tables or columns, so it must read as "no posture cache", which SKILL.md refreshes on
    assert ingest().returncode == 0
    con = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "posture.db")
    con.execute("PRAGMA user_version = 0")
    con.close()
    for cmd in (
        ("distill.py", "index"),
        ("scope.py", "check"),
        ("posture.py",),
        ("ingest.py", "collections", pristine_fx / "collections.json"),
    ):
        r = run(*cmd)
        assert r.returncode != 0, cmd
        assert "no posture cache this skill can read" in r.stderr, (cmd, r.stderr)
        assert "Traceback" not in r.stderr, cmd
    assert ingest().returncode == 0
    assert run("distill.py", "index").returncode == 0


# The posture db's layout as of POSTURE_LAYOUT; see the test below.
SCHEMA_SHA = {6: "77908d56898c1c84"}


def test_a_posture_layout_change_bumps_the_stamp():
    # a changed SCHEMA with an unchanged POSTURE_LAYOUT would let an old posture.db through to
    # queries it can't answer: bump _cache.POSTURE_LAYOUT, then record the new hash here
    sha = hashlib.sha256(ingest_py.SCHEMA.encode()).hexdigest()[:16]
    assert SCHEMA_SHA.get(ingest_py.POSTURE_LAYOUT) == sha, (
        f"ingest.SCHEMA changed: bump _cache.POSTURE_LAYOUT and set SCHEMA_SHA[{ingest_py.POSTURE_LAYOUT}] "
        f"= {sha!r}"
    )


def test_collections_need_a_posture(collections, fx):
    edit_response(fx, lambda r: r["data"].update(posture_x="Query"))
    r = collections(fx)
    assert r.returncode != 0
    assert "no posture cache" in r.stderr


def test_a_posture_refresh_never_touches_policy_content(run, ingest, cache, fx):
    assert ingest().returncode == 0
    before = (cache / FIXTURE_DEPLOYMENT / "guardrails.db").read_bytes()
    r = run("ingest.py", "posture", fx / "posture.json")
    assert r.returncode == 0, r.stderr
    assert (cache / FIXTURE_DEPLOYMENT / "guardrails.db").read_bytes() == before


@pytest.mark.parametrize(
    ("dynamic_filter", "want"),
    [
        (org_filter("o-a"), ("o-a", None)),
        (org_filter("o-a", "/Apps/"), ("o-a", "/Apps/")),
        ("org:o-a path:/SAAS", ("o-a", "/SAAS")),
        ("org:o-a", ("o-a", None)),
        ('{"single":{"name":"org","operator":"equals","value":"o-a"}}', ("o-a", None)),
        ("", (None, None)),
        (None, (None, None)),
        ("Sandbox", (None, None)),
        ("tag:env=de", (None, None)),
        (
            '{"single":{"name":"tag:team","operator":"equals","values":["x"]}}',
            (None, None),
        ),
        (
            '{"multiple":{"operator":"OR","operands":[{"single":{"name":"org","operator":"equals","values":["o-a"]}}]}}',
            (None, None),
        ),
        (
            '{"multiple":{"operator":"AND","operands":[{"single":{"name":"org","operator":"equals","values":["o-a"]}},{"single":{"name":"name","operator":"starts with","values":["x"]}}]}}',
            (None, None),
        ),
        ('{"foo":"bar"}', (None, None)),
    ],
)
def test_only_the_generated_org_filters_are_read(dynamic_filter, want):
    assert ingest_py.org_scope(dynamic_filter) == want


def test_an_older_postures_ingest_waits_for_a_current_one_being_written(
    run, fx, tmp_path, cache, open_cache
):
    # two sessions: B stores the current posture's membership while A, holding an older posture's
    # response, is between its check and its write
    import threading

    ingest_posture_only(run, fx, tmp_path)
    now = refresh_id(cache)
    c = json.loads((fx / "collections.json").read_text())["data"][
        alias_of(cid("CIS AWS Baseline"))
    ]
    b = open_cache()
    b.execute("BEGIN IMMEDIATE")
    b.execute("INSERT OR REPLACE INTO main.membership VALUES (?, ?)", (c["uuid"], now))
    raised = []

    def load():
        a = open_cache()  # its own thread's: sqlite connections stay in theirs
        try:
            with a:
                ingest_py.load_collection(a, c, [], "older")
        except ValueError as e:
            raised.append(str(e))
        finally:
            a.close()

    t = threading.Thread(target=load)
    t.start()
    t.join(0.5)
    assert (
        t.is_alive()
    )  # waiting for B's lock, not deciding on what it read before B wrote
    b.commit()
    t.join(10)
    assert raised
    assert "older posture" in raised[0]
    (posture,) = b.execute(
        "SELECT posture FROM membership WHERE collection_uuid = ?", (c["uuid"],)
    ).fetchone()
    assert posture == now


def test_an_empty_collection_is_never_due(run, ingest, fx, tmp_path):
    add_empty_collection(fx)
    assert ingest(fx).returncode == 0
    # nor after a refresh, which makes every fetched membership due again
    assert ingest(posture_only(fx, tmp_path / "po")).returncode == 0
    assert "uuid-empty" not in {x["uuid"] for x in pending(run)}
    entry = json.loads(run("scope.py", "check").stdout)["in_scope"]["uuid-empty"]
    assert (entry["loaded"], entry["checked"]) == (True, True)


@pytest.mark.parametrize("where", ["platform", "posture_x"])
def test_an_error_about_the_platform_or_posture_refuses_the_response(
    run, fx, tmp_path, where
):
    ingest_posture_only(run, fx, tmp_path)
    edit_response(
        fx, lambda r: r.update(errors=[{"message": "boom", "path": [where, "x"]}])
    )
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode != 0
    assert "the platform reported errors" in r.stderr
    assert "loaded 0 collection(s)" in r.stdout


def test_a_null_policy_is_said_as_such(run, fx, tmp_path):
    ingest_posture_only(run, fx, tmp_path)
    cis = cid("CIS AWS Baseline")
    edit_collections(
        fx, lambda cs: cs[cis]["policyMappings"]["edges"][0].update(node=None)
    )
    r = run("ingest.py", "collections", fx / "collections.json")
    assert "CIS AWS Baseline: ValueError: a policy came back null" in r.stderr


def test_an_older_postures_ingest_sees_a_posture_swapped_since_it_opened(
    run, fx, tmp_path, cache, open_cache
):
    # A opened the cache under R1; meanwhile B refreshed to R2 and stored X's membership under it.
    # A's R1 response must not replace that, though A's attached posture.db is still R1's file.
    ingest_posture_only(run, fx, tmp_path)
    old = refresh_id(cache)
    a = open_cache()
    assert run("ingest.py", "posture", fx / "posture.json").returncode == 0
    assert run("ingest.py", "collections", fx / "collections.json").returncode == 0
    now = refresh_id(cache)
    assert now != old
    c = json.loads((fx / "collections.json").read_text())["data"][
        alias_of(cid("CIS AWS Baseline"))
    ]
    with pytest.raises(ValueError, match="older posture"), a:
        ingest_py.load_collection(a, c, [], old)
    a.close()
    con = sqlite3.connect(cache / FIXTURE_DEPLOYMENT / "guardrails.db")
    (posture,) = con.execute(
        "SELECT posture FROM membership WHERE collection_uuid = ?", (c["uuid"],)
    ).fetchone()
    con.close()
    assert posture == now


def test_a_collection_under_another_collections_alias_is_refused(run, fx, tmp_path):
    ingest_posture_only(run, fx, tmp_path)
    cis, acme = alias_of(cid("CIS AWS Baseline")), alias_of(cid("Acme Org Standards"))
    edit_response(fx, lambda r: r["data"].update({cis: r["data"][acme]}))
    r = run("ingest.py", "collections", fx / "collections.json")
    assert r.returncode != 0
    assert (
        "CIS AWS Baseline: the platform failed: its alias isn't its uuid's" in r.stderr
    )
    # stored under neither: Acme's own alias still loads it
    assert "CIS AWS Baseline" in {x["name"] for x in pending(run)}


def test_a_response_with_two_posture_aliases_is_refused(run, fx, tmp_path):
    ingest_posture_only(run, fx, tmp_path)
    edit_response(fx, lambda r: r["data"].update(posture_a="Query", posture_b="Query"))
    r = run("ingest.py", "collections", fx / "collections.json", stamp=False)
    assert r.returncode != 0
    assert "2 posture_<id> aliases" in r.stderr
