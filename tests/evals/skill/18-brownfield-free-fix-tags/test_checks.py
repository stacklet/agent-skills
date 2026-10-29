"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import check, run


def test_eval18_fails_copying_the_classification_silently(tmp_path):
    edits = {
        "db.tf": lambda t: t.replace(
            'tags = {\n    team = "orders"\n  }',
            'tags = merge(local.finops_tags, {\n    team        = "orders"\n    backup-plan = "daily"\n  })',
        )
    }
    reply = "Tagged it backup-plan = daily, and merged in the repo's FinOps tags. It isn't encrypted."
    out = run(tmp_path, reply, edits)
    assert not check("brownfield-free-fix-tags", out, "If it gave the database")
    told = reply + " That sets data-classification to the repo's default, internal."
    out = run(tmp_path / "told", told, edits)
    assert check("brownfield-free-fix-tags", out, "If it gave the database")


def test_eval18_passes_taking_cost_center_and_asking_for_the_classification(tmp_path):
    edits = {
        "db.tf": lambda t: t.replace(
            'tags = {\n    team = "orders"\n  }',
            'tags = {\n    team        = "orders"\n    backup-plan = "daily"\n'
            '    cost-center = local.finops_tags["cost-center"]\n  }',
        )
    }
    reply = (
        "Tagged it backup-plan = daily and added cost-center from locals.tf. The data-classification "
        "is a judgement about the data: what should it be? The database isn't encrypted; fixing that "
        "replaces it."
    )
    out = run(tmp_path, reply, edits)
    for starts in (
        "If it gave the database",
        "Either added",
        "Changed nothing but",
        "If it added",
    ):
        assert check("brownfield-free-fix-tags", out, starts), starts
