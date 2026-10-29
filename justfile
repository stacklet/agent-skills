# lint tree
lint:
    uv run pre-commit run --all-files

# run tests
test *args:
    uv run pytest {{args}}

# run the evals: agent sessions that cost money, so it prints the plan unless given --yes
evals out *args:
    uv run python3 tests/evals/run_skill.py {{out}} {{args}}

# run the posture assessment's evals: model calls that cost money, so it prints the plan unless given --yes
assess-evals out *args:
    uv run python3 tests/evals/run_assess.py {{out}} {{args}}

# run the distillation's evals: model calls that cost money, so it prints the plan unless given --yes
distill-evals out *args:
    uv run python3 tests/evals/run_distill.py {{out}} {{args}}
