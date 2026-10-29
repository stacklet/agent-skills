"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import check, run


GROW = {
    "worker.tf": lambda t: t.replace(
        "size              = 200", "size              = 500"
    )
}


def test_eval14_passes_reporting_the_switch_without_offering_it(tmp_path):
    reply = (
        "Grew the volume to 500GB. It is gp2, which the gp3-only guardrail flags; switching changes "
        "throughput, so I left it."
    )
    out = run(tmp_path, reply, GROW)
    assert check("brownfield-touch-violating-attr", out, "Only the size changed")


def test_eval14_fails_switching_to_gp3_unasked(tmp_path):
    edits = {"worker.tf": lambda t: GROW["worker.tf"](t).replace('"gp2"', '"gp3"')}
    out = run(tmp_path, "Grew the volume to 500GB and moved it from gp2 to gp3.", edits)
    assert not check("brownfield-touch-violating-attr", out, "Only the size changed")
