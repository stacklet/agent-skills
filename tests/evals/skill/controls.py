"""Helpers for the controls: small hand-written runs that a check must fail or must pass.

The grader's regexes were loosened several times after reading replies that were right in other
words; nothing checked that they still failed wrong ones. Each control grades a run (the brownfield
fixture, edited, plus a reply) and asserts what one check says. Add one whenever a check changes.
"""

import json
import shutil

import grade_skill

from grading import ORDERS_REPO


def run(tmp_path, reply, edits=None, repo=True):
    """A run's outputs dir: a copy of the brownfield fixture (unless REPO is false) with EDITS
    ({file: new text, or a function of the old text}) applied, and REPLY as the final response."""
    out = tmp_path / "outputs"
    if repo:
        shutil.copytree(ORDERS_REPO, out)
    else:
        out.mkdir()
    for name, edit in (edits or {}).items():
        p = out / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(edit(p.read_text()) if callable(edit) else edit, encoding="utf-8")
    (out / "final_response.md").write_text(reply, encoding="utf-8")
    return out


def check(name, out, starts):
    """The result of eval NAME's check whose text starts with STARTS (exactly one must)."""
    found = [
        ok for text, ok in grade_skill.checks(name, out) if text.startswith(starts)
    ]
    assert len(found) == 1, f"{len(found)} checks start with {starts!r}"
    return bool(found[0])


def transcript(out, *calls):
    """Write a transcript beside OUT whose assistant made CALLS ((tool, input) pairs, or (tool,
    input, True) for a sub-agent's: a background sync's), in order."""
    lines = []
    for call in calls:
        name, inp, sub = (*call, False)[:3]
        lines.append(
            json.dumps(
                {
                    "type": "assistant",
                    "parent_tool_use_id": "sync" if sub else None,
                    "message": {
                        "content": [{"type": "tool_use", "name": name, "input": inp}]
                    },
                }
            )
        )
    (out.parent / "transcript.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def flow(out, log, state):
    """Write beside OUT what a flow eval's run leaves: the fake server's LOG (a list of entries) and
    the cache STATE (run_skill.cache_state's)."""
    (out.parent / "mcp_log.jsonl").write_text(
        "".join(json.dumps(x) + "\n" for x in log), encoding="utf-8"
    )
    (out.parent / "cache_state.json").write_text(json.dumps(state), encoding="utf-8")


def bash(command, sub=False):
    """A Bash call, for transcript()."""
    return ("Bash", {"command": command}, sub)


def write(path, content):
    """A Write call, for transcript()."""
    return ("Write", {"file_path": path, "content": content}, False)
