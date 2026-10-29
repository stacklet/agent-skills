# Distilling with another agent's model

`distill.py show` (and the posture assessment, `assess.py show`) runs a model through `claude -p`
by default. Outside Claude Code, the user sets `STACKLET_GUARDRAILS_INFERENCE_CMD` to a JSON list,
e.g. `["llm", "-m", "{model}"]`, of a command that reads the prompt on stdin and writes its answer,
as the prompt asks for it, to stdout, both UTF-8.

- `{model}` becomes the task's model, which defaults to claude's names for them (`haiku` to distil,
  `sonnet` to assess): another program usually needs its own names set too
  (`STACKLET_GUARDRAILS_DISTILL_MODEL`, `STACKLET_GUARDRAILS_ASSESS_MODEL`).
- Its program is a name on PATH or an absolute path, as are any paths in it: it runs, as given, from
  an empty directory.
- API keys go in the environment, not in that list: the list is recorded beside what it made.
