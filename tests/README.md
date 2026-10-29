# Tests

Kept outside the plugin so installs stay lean. `just test` runs them (pytest via uv; extra args are
passed through, e.g. `just test tests/test_ingest.py -k pending`), with no model calls. The evals,
which make real model calls, are in `tests/evals/` (`tests/evals/README.md`).

By area:
- `test_ingest.py`: ingest failure paths, collections and pending fetches, account totals,
  missing-cache behaviour, org filter parsing;
- `test_deployments.py`: deployment namespacing across scripts (the DEPLOYMENT argument, isolation,
  responses from another deployment refused, the deployment in the scope file, `deployments.py`,
  unsafe names);
- `test_digest.py` (the posture digest), `test_filters.py` (group filters in words, and which groups
  hold which), `test_estate.py` (the estate fixture's shapes);
- `test_assess.py` (the posture assessment, with a stub model), `test_scope.py` (scope-file
  validation, answers, decisions, accounts, the notes `index` and `show` print);
- `test_distill.py` (output validation, bundling, per-collection distillation with a stub `claude`),
  `test_inference.py` (which command each task runs), `test_cache.py` (action names);
- the eval harnesses, with no model call: `test_run_skill.py` (the skill eval runner's preparation
  and isolation), `test_run_assess.py`, `test_run_distill.py` (each with every eval's controls),
  `test_fake_mcp.py` (the flow evals' fake Stacklet MCP server, driven through a sync by hand), and
  under `tests/evals/skill/`, `test_grading.py` and each eval's `test_checks.py`.

Shared fixtures are in `conftest.py`. `fixture/` is shared by these tests and the eval runners:
`make_fixture.py` (a small synthetic posture; its `--stamp` marks a collections response as fetched
under a cache's posture, as the skill's queries do), `make_estate.py` (one shaped like a real large
estate's), `fake_stacklet_mcp.py`, and the repos the skill's evals start from.
