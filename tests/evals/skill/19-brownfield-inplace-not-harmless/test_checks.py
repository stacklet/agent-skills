"""Controls for this eval's checks: the CMK gap and its cost, in a real reply's words."""

from controls import check, run


def test_eval19_passes_plain_wording_for_the_cmk_gap_and_its_readers(tmp_path):
    reply = (
        "Turned on versioning for the reports bucket.\n\n"
        "- **Encryption key:** the bucket uses S3-managed encryption, but the rules want a "
        "customer-managed KMS key. Switching means creating or choosing a key and giving everything "
        "that reads or writes reports access to it. I can set it up if you want."
    )
    out = run(tmp_path, reply)
    assert check(
        "brownfield-inplace-not-harmless",
        out,
        "Final response tells the user the bucket misses",
    )
    assert check(
        "brownfield-inplace-not-harmless", out, "Final response says what switching"
    )
