"""The flow evals' fake Stacklet MCP server (fixture/fake_stacklet_mcp.py) answers what the skill
sends, in the shapes the scripts ingest: a sync done by hand against it fills the cache."""

import json
import subprocess
import sys

from conftest import HERE, cid


SERVER = HERE / "fixture" / "fake_stacklet_mcp.py"
PLATFORM = HERE.parent / "plugins" / "stacklet" / "skills" / "guardrails" / "platform"


def serve(fx, log, calls, fail=(), args=(), raw=False):
    """Run the server over FX (with ARGS), make CALLS ([(query, variables)]) in one session -> their
    results' bodies (the tool's text, parsed; with RAW, the JSON-RPC replies)."""
    msgs = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    ] + [
        {
            "jsonrpc": "2.0",
            "id": i,
            "method": "tools/call",
            "params": {
                "name": "platform_graphql_query",
                "arguments": {"query": q, "variables": v},
            },
        }
        for i, (q, v) in enumerate(calls, 2)
    ]
    fail_args = ["--fail", *fail] if fail else []
    r = subprocess.run(
        [sys.executable, str(SERVER), str(fx), str(log), *args, *fail_args],
        input="".join(json.dumps(m) + "\n" for m in msgs),
        capture_output=True,
        text=True,
        check=True,
    )
    replies = {m["id"]: m for m in map(json.loads, r.stdout.splitlines())}
    assert replies[1]["result"]["tools"][0]["name"] == "platform_graphql_query"
    if raw:
        return [replies[i] for i in range(2, 2 + len(calls))]
    return [
        json.loads(replies[i]["result"]["content"][0]["text"])
        for i in range(2, 2 + len(calls))
    ]


def save(body, path):
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def query(name):
    return (PLATFORM / name).read_text(encoding="utf-8")


def test_a_sync_against_it_fills_the_cache(run, pristine_fx, tmp_path):
    log = tmp_path / "log.jsonl"
    (dep, posture) = serve(
        pristine_fx,
        log,
        [(query("deployment.graphql"), {}), (query("posture.graphql"), {})],
    )
    r = run(
        "deployments.py",
        "--console",
        dep["data"]["platform"]["urls"]["console"],
        deployment=None,
    )
    assert json.loads(r.stdout)["use"] == "console.fixture.example"
    assert (
        run("ingest.py", "posture", save(posture, tmp_path / "p.json")).returncode == 0
    )
    q = run("scope.py", "query").stdout
    (collections,) = serve(pristine_fx, log, [(q, {})])
    r = run(
        "ingest.py",
        "collections",
        save(collections, tmp_path / "c.json"),
        stamp=False,
    )
    assert r.returncode == 0, r.stderr
    assert run("scope.py", "query").stdout == ""
    (account,) = serve(
        pristine_fx,
        log,
        [(query("account.graphql"), {"provider": "AWS", "key": "111111111111"})],
    )
    r = run("ingest.py", "accounts", save(account, tmp_path / "a.json"))
    assert r.returncode == 0, r.stderr
    logged = [json.loads(x) for x in log.read_text().splitlines()]
    assert [x["kind"] for x in logged] == [
        "deployment",
        "posture",
        "collections",
        "account",
    ]
    # each the skill's own query
    assert all(x["exact"] for x in logged)


def test_a_collection_it_fails_is_named_and_the_rest_load(run, pristine_fx, tmp_path):
    log = tmp_path / "log.jsonl"
    (posture,) = serve(pristine_fx, log, [(query("posture.graphql"), {})])
    run("ingest.py", "posture", save(posture, tmp_path / "p.json"))
    q = run("scope.py", "query").stdout
    acme = cid("Acme Org Standards")
    (collections,) = serve(pristine_fx, log, [(q, {})], fail=[acme])
    r = run("ingest.py", "collections", save(collections, tmp_path / "c.json"))
    assert r.returncode != 0
    assert "Acme Org Standards: the platform failed" in r.stderr
    assert "CIS AWS Baseline" in r.stdout
    entry = json.loads(log.read_text().splitlines()[-1])
    assert entry["kind"] == "collections"
    assert entry["errors"] == 1
    assert acme in entry["collections"]


def test_variables_may_come_as_json_text(pristine_fx, tmp_path):
    # as the real tool takes them
    (account,) = serve(
        pristine_fx,
        tmp_path / "log",
        [
            (
                query("account.graphql"),
                json.dumps({"provider": "AWS", "key": "111111111111"}),
            )
        ],
    )
    assert account["data"]["account"]["key"] == "111111111111"


def test_a_query_the_skill_didnt_write_is_logged_as_such(pristine_fx, tmp_path):
    log = tmp_path / "log"
    edited = query("posture.graphql").replace("lastDeployed", "lastDeployed name")
    serve(pristine_fx, log, [(edited, {})])
    assert json.loads(log.read_text())["exact"] is False


def test_a_call_that_fails_is_an_error_not_a_crash(pristine_fx, tmp_path):
    log = tmp_path / "log"
    bad, good = serve(
        pristine_fx,
        log,
        [(query("account.graphql"), "{not json"), (query("deployment.graphql"), {})],
        raw=True,
    )
    assert bad["result"]["isError"] is True
    # the session carries on
    assert "console" in good["result"]["content"][0]["text"]
    assert json.loads(log.read_text().splitlines()[0])["kind"] == "failed"


def test_padding_makes_a_result_big_enough_to_be_saved_to_a_file(
    run, pristine_fx, tmp_path
):
    # Claude Code saves an MCP result over 25,000 tokens: about 100 KB
    (posture,) = serve(
        pristine_fx,
        tmp_path / "log",
        [(query("posture.graphql"), {})],
        args=["--pad", "120"],
    )
    assert len(json.dumps(posture)) > 120 * 1024
    # and it still ingests
    assert (
        run("ingest.py", "posture", save(posture, tmp_path / "p.json")).returncode == 0
    )
