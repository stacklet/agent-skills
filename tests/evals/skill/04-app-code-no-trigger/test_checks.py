"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import check, run, transcript


def test_process_fails_a_script_run_for_app_code(tmp_path):
    out = run(tmp_path, "def upload(): ...", repo=False)
    transcript(
        out,
        (
            "Bash",
            {"command": "python3 /x/guardrails/scripts/distill.py d index"},
        ),
    )
    assert not check("app-code-no-trigger", out, "Ran no guardrail script")
