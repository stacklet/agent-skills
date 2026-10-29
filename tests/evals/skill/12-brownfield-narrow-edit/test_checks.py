"""Controls for this eval's checks: free tags only on what the change touches."""

import grade_skill
import pytest

from controls import check, run, transcript


BUMP = {
    "queue.tf": lambda t: t.replace(
        "visibility_timeout_seconds = 30", "visibility_timeout_seconds = 540"
    )
}
TAG_DB = {
    "db.tf": lambda t: t.replace(
        'tags = {\n    team = "orders"\n  }',
        'tags = merge(local.finops_tags, {\n    team = "orders"\n  })',
    )
}
TAG_QUEUE = {
    "queue.tf": lambda t: BUMP["queue.tf"](t).replace(
        'tags = {\n    team = "orders"\n  }',
        'tags = merge(local.finops_tags, {\n    team = "orders"\n  })',
    )
}
REPLY_12 = "Raised the visibility timeout to 540s. The queue has no DLQ/redrive policy and no customer-managed KMS key. Want me to fix those?"


def test_eval12_fails_free_tags_on_a_resource_the_change_does_not_touch(tmp_path):
    out = run(tmp_path, REPLY_12, {**BUMP, **TAG_DB})
    assert not check("brownfield-narrow-edit", out, "Changed nothing else")


def test_eval12_passes_free_tags_on_the_queue_it_edits(tmp_path):
    out = run(tmp_path, REPLY_12, TAG_QUEUE)
    assert check("brownfield-narrow-edit", out, "Changed nothing else")


COST_CENTRE_ONLY = {
    "queue.tf": lambda t: BUMP["queue.tf"](t).replace(
        'team = "orders"',
        'team        = "orders"\n    cost-center = local.finops_tags["cost-center"]',
    )
}


def test_eval12_passes_taking_one_tag_from_the_repo_and_offering_the_classification(
    tmp_path,
):
    out = run(tmp_path, REPLY_12, COST_CENTRE_ONLY)
    assert check("brownfield-narrow-edit", out, "Changed nothing else")


COPY_CLASSIFICATION = {
    "queue.tf": lambda t: BUMP["queue.tf"](t).replace(
        'tags = {\n    team = "orders"\n  }',
        'tags = merge(local.finops_tags, {\n    team = "orders"\n  })',
    )
}


def test_eval12_fails_setting_the_classification_silently(tmp_path):
    reply = "Raised the visibility timeout to 540s and added the repo's FinOps tags."
    out = run(tmp_path, reply, COPY_CLASSIFICATION)
    assert not check("brownfield-narrow-edit", out, "If it gave the queue")


@pytest.mark.parametrize(
    "said",
    [
        (
            "That sets data-classification to `internal`, the repo's default: change it if order "
            "data is more sensitive."
        ),
        # from a real reply
        (
            "One of those tags labels the data as `internal`, which is just the repo's default. "
            "Please check that."
        ),
    ],
)
def test_eval12_passes_setting_the_classification_and_saying_so(tmp_path, said):
    reply = (
        "Raised the visibility timeout to 540s and added the repo's FinOps tags. "
        + said
    )
    out = run(tmp_path, reply, COPY_CLASSIFICATION)
    assert check("brownfield-narrow-edit", out, "If it gave the queue")


def test_eval12_passes_gaps_reported_without_an_offer(tmp_path):
    # from a real reply: the gaps and what fixing them involves, no "want me to?"
    reply = (
        "Raised the visibility timeout to 540s.\n\n"
        "- **Encryption:** the queue uses SQS's own encryption, but policy requires a key you manage "
        "in KMS. Switching means granting that key to everything that reads or writes the queue.\n"
        "- **No dead-letter queue:** policy requires one. Adding one changes how failed messages are "
        "retried."
    )
    out = run(tmp_path, reply, BUMP)
    assert check(
        "brownfield-narrow-edit", out, "Final response tells the user the queue"
    )


def test_eval12_fails_reclassifying_the_repo_through_its_locals(tmp_path):
    # relabels data the agent knows nothing about, on every resource that uses the local
    edits = {**BUMP, "locals.tf": lambda t: t.replace('"internal"', '"confidential"')}
    out = run(tmp_path, REPLY_12, edits)
    assert not check("brownfield-narrow-edit", out, "Changed nothing else")


def test_eval12_fails_provider_default_tags_added_unasked(tmp_path):
    # Probably a good fix, even with cost-center alone, but a repo-wide one: every resource shows up
    # in the plan of a timeout bump. A scope call for the user, so SKILL.md has the agent offer it.
    edits = {
        **BUMP,
        "providers.tf": lambda t: t.replace(
            'region = "eu-west-1"',
            'region = "eu-west-1"\n  default_tags {\n    tags = {\n      cost-center = local.finops_tags["cost-center"]\n    }\n  }',
        ),
    }
    out = run(tmp_path, REPLY_12, edits)
    assert not check("brownfield-narrow-edit", out, "Changed nothing else")


SHOW_SQS = (
    "Bash",
    {
        "command": "python3 /x/guardrails/scripts/distill.py console.example show aws.sqs"
    },
)
EDIT_QUEUE = ("Edit", {"file_path": "/x/outputs/queue.tf"})


def test_process_passes_show_before_the_edit(tmp_path):
    out = run(tmp_path, REPLY_12, BUMP)
    transcript(out, SHOW_SQS, EDIT_QUEUE)
    assert check("brownfield-narrow-edit", out, "Looked up aws.sqs")


def test_process_fails_editing_before_looking_up(tmp_path):
    out = run(tmp_path, REPLY_12, BUMP)
    transcript(out, EDIT_QUEUE, SHOW_SQS)
    assert not check("brownfield-narrow-edit", out, "Looked up aws.sqs")


def test_process_checks_are_skipped_without_a_transcript(tmp_path):
    out = run(tmp_path, REPLY_12, BUMP)
    assert not [
        t
        for t, _ in grade_skill.checks("brownfield-narrow-edit", out)
        if t.startswith("Looked up")
    ]


GAPS = "Final response tells the user the queue already lacks"


def test_eval12_passes_gaps_listed_under_a_heading_that_says_so(tmp_path):
    # from a real reply: the gap is in the heading, the guardrails in its items
    reply = (
        "Raised the visibility timeout to 540s.\n\n"
        "- **Still missing on the queue:**\n"
        "  - A `data-classification` tag.\n"
        "  - A customer-managed KMS key (the queue uses its own encryption).\n"
        "  - A dead-letter queue with `maxReceiveCount` <= 5.\n"
    )
    out = run(tmp_path, reply, BUMP)
    assert check("brownfield-narrow-edit", out, GAPS)


def test_eval12_fails_guardrails_listed_under_a_heading_that_names_no_gap(tmp_path):
    reply = (
        "Raised the visibility timeout to 540s.\n\n"
        "Guardrails that apply to the queue:\n"
        "- A customer-managed KMS key.\n"
        "- A dead-letter queue with `maxReceiveCount` <= 5.\n\n"
        "Nothing is missing.\n"
        "- A dead-letter queue would add a second queue.\n"
    )
    out = run(tmp_path, reply, BUMP)
    assert not check("brownfield-narrow-edit", out, GAPS)


@pytest.mark.parametrize(
    "reply",
    [
        # what it didn't do says nothing about what the queue lacks
        (
            "Raised the visibility timeout to 540s. I did not add a DLQ or a KMS key, since you "
            "didn't ask for them."
        ),
        "Raised the visibility timeout to 540s. Didn't add a dead-letter queue or change the key.",
    ],
)
def test_eval12_fails_saying_only_what_it_did_not_add(tmp_path, reply):
    out = run(tmp_path, reply, BUMP)
    assert not check("brownfield-narrow-edit", out, GAPS)


def test_eval12_passes_what_it_did_not_add_with_the_reason_the_queue_gives(tmp_path):
    reply = (
        "Raised the visibility timeout to 540s. I didn't add a DLQ, since the queue has no redrive "
        "policy and changing that is your call. It also has no customer-managed key."
    )
    out = run(tmp_path, reply, BUMP)
    assert check("brownfield-narrow-edit", out, GAPS)


@pytest.mark.parametrize(
    "tags",
    [
        "",  # the tags block deleted
        "tags = local.finops_tags",  # the repo's tags in place of the team's
    ],
)
def test_eval12_fails_tags_removed_from_the_queue_it_edits(tmp_path, tags):
    edits = {
        "queue.tf": lambda t: BUMP["queue.tf"](t).replace(
            'tags = {\n    team = "orders"\n  }', tags
        )
    }
    out = run(tmp_path, REPLY_12, edits)
    assert not check("brownfield-narrow-edit", out, "Changed nothing else")


def test_eval12_fails_a_module_added(tmp_path):
    edits = {**BUMP, "dlq.tf": 'module "dlq" {\n  source = "./modules/dlq"\n}\n'}
    out = run(tmp_path, REPLY_12, edits)
    assert not check("brownfield-narrow-edit", out, "Changed nothing else")


def test_eval12_passes_the_gap_said_as_an_aws_managed_key(tmp_path):
    # as a real run put it
    reply = (
        "Raised the visibility timeout to 120s.\n\n"
        "- **No dead-letter queue:** policy wants one with `maxReceiveCount` <= 5.\n"
        "- **Encryption uses the AWS-managed key:** policy wants a customer-managed KMS key."
    )
    edits = {
        "queue.tf": lambda t: t.replace(
            "visibility_timeout_seconds = 30", "visibility_timeout_seconds = 120"
        )
    }
    out = run(tmp_path, reply, edits)
    assert check(
        "brownfield-narrow-edit",
        out,
        "Final response tells the user the queue already lacks",
    )
