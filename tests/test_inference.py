import json

import _inference
import pytest


@pytest.fixture
def env(monkeypatch):
    for k in ("INFERENCE_CMD", "DISTILL_MODEL", "ASSESS_MODEL"):
        monkeypatch.delenv(f"STACKLET_GUARDRAILS_{k}", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
    return lambda k, v: monkeypatch.setenv(f"STACKLET_GUARDRAILS_{k}", v)


def stub(tmp_path, body):
    """An executable `claude` in tmp_path: a Python script of BODY."""
    exe = tmp_path / "claude"
    exe.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    exe.chmod(0o755)
    return exe


def default(model):
    """The default Command for MODEL."""
    return _inference.Command(["claude", *_inference.default_args(model)], custom=False)


def claude_at(exe):
    """The default Command (haiku), run as EXE."""
    return _inference.Command([str(exe), *_inference.default_args("haiku")], False)


def test_each_task_defaults_to_isolated_claude_with_its_own_model(env):
    assert _inference.command("distill") == default("haiku")
    assert _inference.command("assess") == default("sonnet")


def test_a_model_setting_keeps_the_default_program_and_is_not_custom(env):
    env("ASSESS_MODEL", "opus")
    cmd = _inference.command("assess")
    assert cmd == default("opus")
    assert _inference.command("distill") == default("haiku")
    assert (
        _inference.made_by({"inference_cmd": cmd.recorded(), "model": "Opus"}) == "Opus"
    )


def test_a_custom_program_takes_each_task_s_model_where_it_asks_for_one(env):
    env("INFERENCE_CMD", '["llm", "-m", "{model}", "--tag=t-{model}"]')
    env("ASSESS_MODEL", "big")
    assert _inference.command("assess").argv == ["llm", "-m", "big", "--tag=t-big"]
    assert _inference.command("distill").argv == ["llm", "-m", "haiku", "--tag=t-haiku"]
    env("INFERENCE_CMD", '["mine", "-x"]')  # its own model
    cmd = _inference.command("assess")
    assert cmd == _inference.Command(["mine", "-x"], custom=True)
    row = {"inference_cmd": cmd.recorded(), "model": None}
    assert _inference.made_by(row) == "mine (custom command)"


def test_a_custom_command_is_custom_whatever_its_arguments(env):
    # a repo's settings can set the variable: copying the default's arguments mustn't pass it off
    # as the default, whose output is served to every repo unmarked
    env(
        "INFERENCE_CMD",
        json.dumps(["/repo/claude", *_inference.default_args("{model}")]),
    )
    cmd = _inference.command("distill")
    assert cmd.custom
    row = {"inference_cmd": cmd.recorded(), "model": "Claude Haiku"}
    assert _inference.made_by(row) == "Claude Haiku (custom command)"


@pytest.mark.parametrize("bad", ["llm -p", "[]", '["llm", 1]', '{"cmd": "llm"}'])
def test_an_invalid_command_is_refused(env, bad):
    env("INFERENCE_CMD", bad)
    with pytest.raises(ValueError, match="STACKLET_GUARDRAILS_INFERENCE_CMD must be"):
        _inference.command("assess")


def test_the_default_command_s_envelope_gives_answer_tokens_and_cost(tmp_path):
    # stands in for claude: whatever the arguments, the envelope `--output-format json` gives
    exe = stub(
        tmp_path,
        "import json, sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({'type': 'result', 'is_error': False, 'result': ' the answer ',"
        " 'total_cost_usd': 0.02, 'usage': {'input_tokens': 9, 'cache_creation_input_tokens': 900,"
        " 'cache_read_input_tokens': 91, 'output_tokens': 40}}))\n",
    )
    text, stats = _inference.run("q", claude_at(exe))
    assert text == "the answer"
    assert stats["input_tokens"] == 1000
    assert stats["output_tokens"] == 40
    assert stats["cost_usd"] == 0.02
    # a custom command: its output is the answer, and only time is known
    text, stats = _inference.run("q", _inference.Command([str(exe)], custom=True))
    assert text.startswith('{"type": "result"')
    assert set(stats) == {"seconds"}


def test_an_envelope_reporting_an_error_is_one(tmp_path):
    exe = stub(
        tmp_path,
        "import json\nprint(json.dumps({'is_error': True, 'result': 'overloaded'}))\n",
    )
    with pytest.raises(
        _inference.InferenceError, match="reported an error: overloaded"
    ):
        _inference.run(
            "q",
            claude_at(exe),
        )


def test_a_failing_default_command_says_why_from_its_envelope(tmp_path):
    # in JSON mode claude reports a failure on stdout, in the envelope, not on stderr
    exe = stub(
        tmp_path,
        "import json, sys\n"
        "print(json.dumps({'is_error': True, 'result': 'API Error: 529 overloaded'}))\n"
        "sys.exit(1)\n",
    )
    with pytest.raises(
        _inference.InferenceError, match=r"failed \(exit 1\): API Error: 529 overloaded"
    ):
        _inference.run(
            "q",
            claude_at(exe),
        )


def test_a_program_that_cannot_run_names_the_setting(tmp_path):
    with pytest.raises(
        _inference.InferenceError, match="set STACKLET_GUARDRAILS_INFERENCE_CMD"
    ):
        _inference.run(
            "q",
            _inference.Command(
                [str(tmp_path / "missing"), *_inference.default_args("haiku")], False
            ),
        )


def test_stats_are_summed_over_attempts_and_described():
    both = _inference.combined(
        {"seconds": 1.5, "input_tokens": 10, "output_tokens": 2},
        {"seconds": 2.0, "input_tokens": 1000, "output_tokens": 3},
    )
    assert both == {
        "attempts": 2,
        "seconds": 3.5,
        "input_tokens": 1010,
        "output_tokens": 5,
    }
    assert _inference.describe(both) == "4s, 1,010 tokens in, 5 out, 2 attempts"
    assert _inference.describe(None) == ""


@pytest.mark.parametrize(
    "envelope",
    [
        "{'is_error': False, 'result': 'ok', 'usage': {'input_tokens': [1], 'output_tokens': 'x'}}",
        "{'is_error': False, 'result': 'ok', 'usage': 'none', 'total_cost_usd': 'free'}",
    ],
)
def test_odd_usage_values_count_nothing_rather_than_fail(tmp_path, envelope):
    # what a call cost is noted, never relied on
    exe = stub(tmp_path, f"import json\nprint(json.dumps({envelope}))\n")
    text, stats = _inference.run("q", claude_at(exe))
    assert text == "ok"
    assert (stats["input_tokens"], stats["output_tokens"]) == (0, 0)
    assert "cost_usd" not in stats
    assert _inference.describe(_inference.combined(stats)).endswith(
        "0 tokens in, 0 out"
    )


def test_no_envelope_from_the_default_command_is_an_error(tmp_path):
    exe = stub(tmp_path, "print('just text')\n")
    with pytest.raises(_inference.InferenceError, match="wrote no result envelope"):
        _inference.run(
            "q",
            claude_at(exe),
        )
