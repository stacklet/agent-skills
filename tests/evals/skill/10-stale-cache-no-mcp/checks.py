import re

from grading import TAGS_OK, has, said_together


SHOWS = ("aws.sqs",)


def checks(d, t, f):
    r = re.search(r"maxReceiveCount\W+(\d+)", t) or re.search(
        r"max_receive_count\W+(\d+)", t
    )
    return [
        (
            "Final response says the cached guardrails may be out of date",
            # "refresh" alone would do: every reply says no Stacklet server was there to refresh
            said_together(
                f,
                r"posture|cache|guardrail|polic|rules|data|copy",
                r"out[- ]of[- ]date|stale|\bold\b|20\d\d-\d\d-\d\d|\b(from|in|since) (((jan|febr)uary|march|april|may|june|july|august|(sept|nov|dec)ember|october) )?20\d\d\b|\d+ (days?|years?)|years? ago",
            ),
        ),
        ("Did not block: wrote the queue", has(r"aws_sqs_queue", t)),
        (
            "Still applied the cached guardrails: redrive policy with maxReceiveCount <= 5 and a CMK",
            bool(r) and int(r.group(1)) <= 5 and has(r"kms_master_key_id\s*=", t),
        ),
        # the fixture's ORG rules
        ("Tags cost-center and data-classification with a valid value", TAGS_OK(t)),
    ]
