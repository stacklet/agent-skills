"""Tests for the helpers in grading.py that no single eval owns."""

from grading import (
    all_exact,
    chained_script_calls,
    first_edit,
    refetched,
)


def test_chained_script_calls_are_counted():
    calls = [
        (
            "Bash",
            {"command": "python3 /x/guardrails/scripts/scope.py d check && echo ok"},
        ),
        (
            "Bash",
            {"command": "python3 /x/guardrails/scripts/distill.py d show aws.s3"},
        ),
        (
            "Bash",
            {"command": "cd /tmp && python3 /x/guardrails/scripts/distill.py d index"},
        ),
    ]
    assert chained_script_calls(calls) == 2


def test_a_run_of_an_eval_that_is_gone_is_skipped(tmp_path, capsys):
    import grade_skill

    out = tmp_path / "eval-99-gone" / "with_skill" / "run-1" / "outputs"
    out.mkdir(parents=True)
    (out / "final_response.md").write_text("done", encoding="utf-8")
    grade_skill.main(tmp_path)
    assert "not graded" in capsys.readouterr().err
    assert not (out.parent / "grading.json").exists()


def test_recording_an_account_counts_and_forgetting_one_does_not():
    from grading import recorded_account_check

    def ok(command):
        [(_, passed)] = recorded_account_check(
            [("Bash", {"command": command})], "with_skill"
        )
        return passed

    scope = "python3 /x/guardrails/scripts/scope.py console.estate.example decide "
    assert ok(scope + "--account AWS:456789012345 --because 'providers.tf'")
    assert not ok(scope + "--forget-account AWS:456789012345")
    assert not ok(scope + "--answer q1=yes --because 'x'")


def test_a_syncs_writes_outside_the_repo_are_not_edits():
    calls = [
        ("Write", {"file_path": "/tmp/tmp.AbC/posture.json"}),
        ("Bash", {"command": "python3 /x/guardrails/scripts/distill.py d show aws.s3"}),
        (
            "Write",
            {
                "file_path": "/tmp/tmpq/work_x/outputs/main.tf",
                "content": 'resource "aws_s3_bucket" "a" {}',
            },
        ),
    ]
    assert first_edit(calls) == 2
    assert first_edit([("Edit", {"file_path": "modules/a/main.tf"})]) == 0


def test_a_terraform_file_defining_no_resource_is_not_an_edit():
    calls = [
        ("Write", {"file_path": "versions.tf", "content": "terraform {\n}\n"}),
        ("Write", {"file_path": "variables.tf", "content": 'variable "x" {}\n'}),
        (
            "Write",
            {"file_path": "main.tf", "content": 'resource "aws_s3_bucket" "a" {}\n'},
        ),
    ]
    assert first_edit(calls) == 2
    # an edit inside an existing resource block names none, and still counts
    assert first_edit([("Edit", {"file_path": "main.tf", "new_string": "x = 1"})]) == 0


def test_refetched_counts_across_queries():
    log = [
        {"kind": "collections", "collections": ["a", "b"]},
        {"kind": "posture"},
        {"kind": "collections", "collections": ["c", "a"]},
    ]
    assert refetched(log) == {"a"}


def shell_edit(command):
    """first_edit of one shell COMMAND."""
    return first_edit([("Bash", {"command": command})])


def test_a_shell_write_counts_only_into_the_repo_and_only_terraform():
    resource = 'resource "aws_s3_bucket" "a" {}'
    # agents write files with heredocs too: a resource written so before `show` is still too early
    calls = [
        ("Bash", {"command": "python3 /x/guardrails/scripts/distill.py d index"}),
        ("Bash", {"command": f"cat > main.tf <<'EOF'\n{resource}\nEOF"}),
    ]
    assert first_edit(calls) == 1
    # but not one defining no resource
    assert shell_edit("cat > versions.tf <<'EOF'\nterraform {}\nEOF") is None
    # nor output to a temp dir, or a sync's own file under a shell variable: not the repo
    assert shell_edit("echo x > /tmp/t/c.json 2>&1") is None
    assert shell_edit('cat > "$D/posture.json" <<EOF\n{}\nEOF') is None
    assert shell_edit(f"cat > $D/main.tf <<EOF\n{resource}\nEOF") is None
    # a script's output, not code
    assert shell_edit("python3 scope.py d query > queries.json") is None
    # every redirect is read: the second here writes the repo's code
    assert (
        shell_edit(f"python3 x > /tmp/a.json && cat > main.tf <<EOF\n{resource}\nEOF")
        == 0
    )


def test_only_the_skills_own_queries_are_exact():
    query = {"tool": "platform_graphql_query", "kind": "posture", "exact": True}
    assert all_exact([query])
    # a query that raised (bad variables) is not the skill's
    failed = {
        "tool": "platform_graphql_query",
        "kind": "failed",
        "error": "ValueError: x",
    }
    assert not all_exact([query, failed])
    assert not all_exact([query, {**query, "exact": False}])
    # the other tools aren't queries, and no query at all isn't a pass
    assert all_exact([query, {"tool": "platform_graphql_info", "kind": "info"}])
    assert not all_exact([])
