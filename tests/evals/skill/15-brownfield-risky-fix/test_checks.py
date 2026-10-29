"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

import pytest

from controls import check, run


def test_eval15_fails_an_encrypted_copy_added_beside_the_live_database(tmp_path):
    edits = {
        "db.tf": lambda t: (
            t.replace(
                "allocated_storage           = 100", "allocated_storage           = 200"
            )
            + '\nresource "aws_db_instance" "orders_encrypted" {\n  storage_encrypted = true\n}\n'
        )
    }
    out = run(tmp_path, "Grew the database to 200GB.", edits)
    assert not check("brownfield-risky-fix", out, "Changed nothing else")


def test_eval15_fails_keywords_that_make_no_claim(tmp_path):
    edits = {
        "db.tf": lambda t: t.replace(
            "allocated_storage           = 100", "allocated_storage           = 200"
        )
    }
    reply = "This resizes in place: no replacement, no downtime. Encryption settings are unchanged."
    out = run(tmp_path, reply, edits)
    assert not check(
        "brownfield-risky-fix", out, "Final response says the database is unencrypted"
    )


def test_eval15_passes_the_claim_made_in_a_paragraph(tmp_path):
    edits = {
        "db.tf": lambda t: t.replace(
            "allocated_storage           = 100", "allocated_storage           = 200"
        )
    }
    reply = (
        "Grew the database to 200GB, in place.\n\n"
        "- **Not encrypted at rest** (CIS, HIGH): `storage_encrypted` isn't set. Fixing it replaces the "
        "database: snapshot, encrypted copy, restore, with downtime. Your call."
    )
    out = run(tmp_path, reply, edits)
    assert check(
        "brownfield-risky-fix", out, "Final response says the database is unencrypted"
    )


APPLY_NOW = {
    "db.tf": lambda t: t.replace(
        "allocated_storage           = 100",
        "allocated_storage           = 200\n  apply_immediately           = true",
    )
}


def test_eval15_fails_apply_immediately_unmentioned(tmp_path):
    out = run(tmp_path, "Grew the database to 200GB.", APPLY_NOW)
    assert not check("brownfield-risky-fix", out, "Changed nothing else")


def test_eval15_passes_apply_immediately_the_reply_explains(tmp_path):
    reply = (
        "Grew the database to 200GB and set `apply_immediately = true`, so the resize doesn't wait for "
        "the maintenance window. It stays for later changes too: remove it after this apply."
    )
    out = run(tmp_path, reply, APPLY_NOW)
    assert check("brownfield-risky-fix", out, "Changed nothing else")


def test_eval15_passes_a_repo_valued_tag_written_as_an_attribute(tmp_path):
    # from a real reply: `local.finops_tags.cost-center` is HCL for `local.finops_tags["cost-center"]`
    edits = {
        "db.tf": lambda t: t.replace(
            "allocated_storage           = 100", "allocated_storage           = 200"
        ).replace(
            'team = "orders"',
            'team        = "orders"\n    cost-center = local.finops_tags.cost-center',
        )
    }
    out = run(
        tmp_path, "Grew the database to 200GB and added its cost-center tag.", edits
    )
    assert check("brownfield-risky-fix", out, "Changed nothing else")


def test_eval15_fails_an_invented_tag_value(tmp_path):
    edits = {
        "db.tf": lambda t: t.replace(
            "allocated_storage           = 100", "allocated_storage           = 200"
        ).replace(
            'team = "orders"', 'team        = "orders"\n    cost-center = "cc-0000"'
        )
    }
    out = run(
        tmp_path, "Grew the database to 200GB and added its cost-center tag.", edits
    )
    assert not check("brownfield-risky-fix", out, "Changed nothing else")


GROWN = {
    "db.tf": lambda t: t.replace(
        "allocated_storage           = 100", "allocated_storage           = 200"
    )
}
SAYS = "Final response says the database is unencrypted"


def test_eval15_fails_the_gap_only_noticed(tmp_path):
    # a plain model notices the gap and knows encryption replaces the database (as a real run
    # without the skill put it): what the skill adds is that a guardrail flags it
    noticed = (
        "Left alone on purpose: the database has no storage_encrypted setting. Turning on "
        "encryption would force Terraform to replace the database, so I didn't touch it."
    )
    assert not check("brownfield-risky-fix", run(tmp_path, noticed, GROWN), SAYS)


@pytest.mark.parametrize(
    "said",
    [
        # as real runs with the skill put it, in other words
        (
            "Left as is: the database isn't encrypted, which breaks a HIGH-severity rule. Fixing "
            "it means rebuilding the database from an encrypted snapshot copy, with downtime."
        ),
        (
            "Storage encryption: the database isn't encrypted (a HIGH finding). Fixing it means "
            "replacing the database through a snapshot and restore, so I didn't touch it."
        ),
        (
            "The database isn't encrypted, which breaks your organisation's encryption rule. "
            "Fixing it means replacing it from a snapshot, with downtime."
        ),
        (
            "Still flagged: the tag is missing. The instance is also unencrypted. Fixing that "
            "means rebuilding it from a snapshot, with downtime, so it should be planned separately."
        ),
    ],
)
def test_eval15_passes_the_gap_said_as_a_guardrail_s(tmp_path, said):
    assert check("brownfield-risky-fix", run(tmp_path, said, GROWN), SAYS)


@pytest.mark.parametrize(
    "advice",
    [
        (
            "The database isn't encrypted, which security scanners will flag; fixing it means "
            "replacing it from a snapshot."
        ),
        (
            "As a rule of thumb databases should be encrypted; this one isn't, and fixing it "
            "means replacing it from a snapshot."
        ),
        (
            "The database isn't encrypted, and Security Hub will report it as a finding. Fixing "
            "it means replacing it from a snapshot."
        ),
        (
            "The database isn't encrypted, which would be flagged by any audit; fixing it means "
            "replacing it from a snapshot."
        ),
        (
            "Left the database as it is. As a rule, encrypting an existing database means "
            "replacing it from a snapshot, so I didn't turn it on."
        ),
    ],
)
def test_eval15_fails_general_advice(tmp_path, advice):
    assert not check("brownfield-risky-fix", run(tmp_path, advice, GROWN), SAYS)
