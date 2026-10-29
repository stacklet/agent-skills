#!/usr/bin/env python3
"""Stands in for `claude` in the tests (conftest.fake_claude puts it on PATH): answers a
distillation with one bullet naming every policy ref in the prompt, and an assessment with every
collection applying, in claude's JSON envelope when called as the default command. FAKE_CLAUDE_*
variables make it misbehave, for the tests of what happens then."""

import atexit
import hashlib
import io
import json
import os
import re
import sys
import time


prompt = sys.stdin.read()

# called as the default command: the answer goes in claude's JSON envelope, with its usage
if "--output-format" in sys.argv and not os.environ.get("FAKE_CLAUDE_LATIN1"):
    real, sys.stdout = sys.stdout, io.StringIO()

    def envelope(answer=sys.stdout):
        usage = {"input_tokens": 90, "cache_read_input_tokens": 10, "output_tokens": 20}
        real.write(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": answer.getvalue(),
                    "total_cost_usd": 0.001,
                    "usage": usage,
                }
            )
        )

    atexit.register(envelope)

refs = re.findall(r'"name": "([^"]+@[0-9]+)"', prompt)
# one line per call, naming who called
with open(os.environ["FAKE_CLAUDE_CALLS"], "a") as calls:
    calls.write(f"{os.getppid()}\n")
time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "0")))

# an assessment (assess.py), whatever the mode: every collection in the digest applies
if "<posture>" in prompt:
    names = re.findall(r"^  - (.+?): [0-9]+ polic", prompt, re.MULTILINE)
    collections = [{"name": n, "default": "applies", "why": "w"} for n in names]
    print(
        json.dumps(
            {
                "model": "fake-model 1.0",
                "notes": [],
                "questions": [],
                "collections": collections,
            }
        )
    )
    sys.exit(0)

if os.environ.get("FAKE_CLAUDE_FAIL_LATIN1"):
    sys.stderr.buffer.write("fichier introuvable: é\n".encode("latin-1"))
    sys.exit(2)
if os.environ.get("FAKE_CLAUDE_LATIN1"):
    bullet = "- naïve [ref: ".encode("latin-1") + " ".join(refs).encode() + b"]\n"
    sys.stdout.buffer.write(bullet)
    sys.exit(0)
if os.environ.get("FAKE_CLAUDE_JUNK"):
    print("I'd rather not.")
    sys.exit(0)
# junk the first time each prompt is seen
if os.environ.get("FAKE_CLAUDE_JUNK_FIRST"):
    seen = os.path.join(
        os.environ["FAKE_CLAUDE_JUNK_FIRST"],
        hashlib.sha256(prompt.encode()).hexdigest(),
    )
    if not os.path.exists(seen):
        with open(seen, "w"):
            pass
        print("I'd rather not.")
        sys.exit(0)

if not os.environ.get("FAKE_CLAUDE_ANONYMOUS"):
    print("Model: fake-model 1.0")
print("- guardrail [ref: " + " ".join(refs) + "]")
