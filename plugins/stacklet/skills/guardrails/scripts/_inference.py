"""Model calls: which command each task runs, how it runs, and how its output is attributed.

Two settings, each answering one question:

  STACKLET_GUARDRAILS_INFERENCE_CMD  which program, for every task (default: claude, isolated:
                                     default_args)
  STACKLET_GUARDRAILS_<TASK>_MODEL   which model a task (distill.py's distillations, assess.py's
                                     posture assessments) uses; defaults in TASKS

The program is a JSON list of strings (no shell-style splitting, so Windows paths survive) that reads
the prompt on stdin and writes the answer on stdout, both UTF-8; "{model}" in it becomes the task's
model. A repo's own settings can set it too, and its output is served to every repo on the
deployment, so a custom program's output is marked as such (made_by); a model setting keeps the
default, isolated program, unmarked. The command is recorded with each output, and arguments are
never secret (ps shows them), so API keys go in the environment.
"""

import json
import os
import re
import subprocess
import tempfile
import time

from typing import NamedTuple


# Each task's default model: distilling one resource type's policies is small work, done often; an
# assessment is one call per posture change, over the whole posture, that every repo then relies on.
TASKS = {"distill": "haiku", "assess": "sonnet"}
INFERENCE_CMD_ENV = "STACKLET_GUARDRAILS_INFERENCE_CMD"


def default_args(model):
    """The default command's arguments: isolated from the repo (settings, CLAUDE.md, MCP servers) and
    from the user's MCP servers too, with no tools and nothing kept."""
    return [
        "-p",
        "--model",
        model,
        "--tools",
        "",
        "--no-session-persistence",
        "--setting-sources",
        "user",
        "--strict-mcp-config",
        # an envelope with the answer and what it cost (tokens, time): recorded beside it
        "--output-format",
        "json",
    ]


class Command(NamedTuple):
    """A task's command: its argv, and whether it is custom (STACKLET_GUARDRAILS_INFERENCE_CMD): the
    default answers in an envelope, a custom one just the answer."""

    argv: list
    custom: bool

    def recorded(self):
        """As stored beside its output (inference_cmd)."""
        return json.dumps({"argv": self.argv, "custom": self.custom})


def command(task):
    """TASK's Command, by the settings in the module docstring; raises ValueError for a setting that
    isn't a JSON list of strings. The default program is CLAUDE_CODE_EXECPATH (the running claude)
    or plain `claude`, run as given, with no lookup of our own: one would search the repo being
    worked on (first, on Windows), so a repo could supply its own `claude`."""
    model = (
        os.environ.get(f"STACKLET_GUARDRAILS_{task.upper()}_MODEL", "").strip()
        or TASKS[task]
    )
    custom = os.environ.get(INFERENCE_CMD_ENV, "").strip()
    if not custom:
        # `or`, not get()'s default: an empty CLAUDE_CODE_EXECPATH would otherwise be the program
        claude = os.environ.get("CLAUDE_CODE_EXECPATH") or "claude"
        return Command([claude, *default_args(model)], custom=False)
    try:
        argv = json.loads(custom)
    except ValueError:
        argv = None
    if not (isinstance(argv, list) and argv and all(isinstance(x, str) for x in argv)):
        raise ValueError(
            f'{INFERENCE_CMD_ENV} must be a JSON list of strings, e.g. ["llm", "-m", "{{model}}"] '
            "(in JSON, a Windows path's backslashes are doubled)"
        )
    return Command([x.replace("{model}", model) for x in argv], custom=True)


class InferenceError(Exception):
    """The command failed; the message says how. Not a ValueError, which means output that validation
    rejected."""


def envelope(stdout):
    """The default command's JSON envelope in STDOUT (bytes), as a dict, or None if there is none."""
    try:
        env = json.loads(stdout.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError too
        return None
    return env if isinstance(env, dict) else None


def run(prompt, cmd, timeout=300):
    """(CMD's answer to PROMPT, what it took: "seconds", and from the default command's envelope
    tokens and cost). UTF-8 both ways whatever the locale; stderr decoded leniently, so an error in
    another encoding is still reported."""
    program = cmd.argv[0]
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="guardrails_inference_") as cwd:
        try:
            r = subprocess.run(
                cmd.argv,
                input=prompt.encode("utf-8"),
                capture_output=True,
                timeout=timeout,
                cwd=cwd,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise InferenceError(
                f"{program} timed out after {timeout:.0f}s; nothing was cached"
            ) from None
        except OSError as e:
            raise InferenceError(
                f"cannot run {program}: {e} (set {INFERENCE_CMD_ENV} to the program with its "
                "full path; for claude, keep the default's arguments too, or it runs without their "
                "isolation)"
            ) from None
    env = None if cmd.custom else envelope(r.stdout)
    # the default command says why it failed in its envelope, on stdout
    reason = str((env or {}).get("result") or "").strip()[:500]
    if r.returncode:
        err = reason or r.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise InferenceError(
            f"{program} failed (exit {r.returncode})" + (f": {err}" if err else "")
        )
    try:
        out = r.stdout.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise InferenceError(
            f"{program} wrote output that isn't UTF-8; nothing was cached"
        ) from None
    stats = {"seconds": round(time.monotonic() - start, 1)}
    if cmd.custom:
        return out, stats
    if env is None:
        raise InferenceError(f"{program} wrote no result envelope; nothing was cached")
    if env.get("is_error") or not isinstance(env.get("result"), str):
        raise InferenceError(f"{program} reported an error: {reason}")
    usage = env.get("usage") if isinstance(env.get("usage"), dict) else {}

    def count(k):
        # what it cost is only noted, never relied on: a value of any other shape counts 0
        v = usage.get(k)
        return v if isinstance(v, int) and not isinstance(v, bool) else 0

    stats["input_tokens"] = sum(
        count(k)
        for k in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )
    stats["output_tokens"] = count("output_tokens")
    if isinstance(env.get("total_cost_usd"), (int, float)):
        stats["cost_usd"] = env["total_cost_usd"]
    return env["result"].strip(), stats


def combined(*stats):
    """STATS (run's) summed over the attempts that made one result, with how many there were."""
    out = {"attempts": len(stats)}
    for s in stats:
        for k, v in s.items():
            if isinstance(v, (int, float)):
                out[k] = round(out.get(k, 0) + v, 4)
    return out


def describe(stats):
    """STATS (combined's) in a few words, for a note; '' if there are none (made by rules alone)."""
    if not stats:
        return ""
    words = [f"{stats.get('seconds', 0):.0f}s"]
    if "input_tokens" in stats:
        words.append(
            f"{stats['input_tokens']:,} tokens in, {stats.get('output_tokens', 0):,} out"
        )
    if stats.get("attempts", 1) > 1:
        words.append(f"{stats['attempts']} attempts")
    return ", ".join(words)


def model_name(text):
    """A model's name for itself, cut down to a short run of harmless characters (model-written,
    shown in notes): room for "Claude Sonnet 5.5 (claude-sonnet-5-5)", not for a sentence aimed at
    the agent."""
    name = re.sub(r"[^A-Za-z0-9 ._:()/-]", "", str(text or "")).strip()[:40].strip()
    return name or None


def made_by(row):
    """Who made cached output (a row with inference_cmd and model), for a note: the model's name for
    itself, else the program's file name (never its arguments), marked when it was a custom command,
    which a repo's settings can set, so it never passes for the default."""
    cmd = json.loads(row["inference_cmd"])
    if not cmd["argv"]:  # nothing was run: rules decided it all
        return row["model"]
    who = row["model"] or os.path.basename(cmd["argv"][0])
    return f"{who} (custom command)" if cmd["custom"] else who
