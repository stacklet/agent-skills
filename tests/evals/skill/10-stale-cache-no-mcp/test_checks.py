"""Controls for this eval's checks: the reply must say the cached guardrails may be out of date."""

import pytest

from controls import check, run


@pytest.mark.parametrize(
    "reply",
    [
        "I worked from the saved copy of your policies, which was last updated in 2020.",
        "The saved copy of your organisation's rules is from 2020.",
        "The cached guardrails are stale (refreshed 2020-01-01).",
        # from a real reply
        "The org's policies came from a local copy that was last updated in January 2020.",
    ],
)
def test_eval10_passes_saying_how_old_the_cache_is(tmp_path, reply):
    out = run(tmp_path, reply, repo=False)
    assert check("stale-cache-no-mcp", out, "Final response says the cached guardrails")


def test_eval10_fails_never_saying_the_cache_is_old(tmp_path):
    reply = "No Stacklet server was available to refresh the rules, so I used the cached ones."
    out = run(tmp_path, reply, repo=False)
    assert not check(
        "stale-cache-no-mcp", out, "Final response says the cached guardrails"
    )
