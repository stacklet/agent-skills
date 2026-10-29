"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

import pytest

from controls import check, run


def test_eval16_fails_guessing_a_concurrency_cap(tmp_path):
    edits = {
        "consumer.tf": lambda t: t.replace(
            "timeout       = 25",
            "timeout       = 25\n  memory_size   = 1024\n  reserved_concurrent_executions = 10",
        )
    }
    out = run(tmp_path, "Set memory to 1024MB and capped concurrency at 10.", edits)
    assert not check(
        "brownfield-hidden-risk-lambda",
        out,
        "Did not add reserved_concurrent_executions",
    )


@pytest.mark.parametrize(
    "lacks",
    [
        "doesn't reserve any concurrency",
        "has no `reserved_concurrent_executions`",
        "has no reserved concurrency",
    ],
)
def test_eval16_passes_ways_of_saying_concurrency_is_missing(tmp_path, lacks):
    edits = {
        "consumer.tf": lambda t: t.replace(
            "timeout       = 25", "timeout       = 25\n  memory_size   = 1024"
        )
    }
    reply = (
        f"Set memory to 1024MB. The function {lacks} and doesn't have X-Ray tracing turned on; "
        "both rules only report problems."
    )
    out = run(tmp_path, reply, edits)
    assert check("brownfield-hidden-risk-lambda", out, "Final response tells the user")
