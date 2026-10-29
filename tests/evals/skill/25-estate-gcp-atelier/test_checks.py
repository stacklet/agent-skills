"""Controls for this eval's checks: outputs they must fail and pass (controls.py says why)."""

from controls import check, run


GOOD = """
resource "google_storage_bucket" "renders" {
  name                        = "atelier-render-outputs"
  location                    = "EUROPE-WEST2"
  uniform_bucket_level_access = true
  versioning {
    enabled = true
  }
  labels = {
    atelier-team = "render"
    cost-code   = "rnd-204"
  }
}
"""
SCOPE = ".stacklet/guardrails/scope.json"
RECORDED = '{"accounts": [{"provider": "GCP", "key": "atelier-render-prod"}]}'


def test_eval25_passes_the_atelier_guardrails(tmp_path):
    out = run(tmp_path, "Done.", {"storage.tf": GOOD, SCOPE: RECORDED}, repo=False)
    for starts in (
        "Recorded the provider",
        "Labels",
        "Versioning",
        "Uniform",
        "Does not ask",
    ):
        assert check("estate-gcp-atelier", out, starts), starts


def test_eval25_fails_missing_labels_and_asking_the_org(tmp_path):
    code = GOOD.replace('cost-code   = "rnd-204"', "")
    reply = "Done. Is this project in the atelier.example organisation?"
    out = run(tmp_path, reply, {"storage.tf": code}, repo=False)
    assert not check("estate-gcp-atelier", out, "Recorded the provider")
    assert not check("estate-gcp-atelier", out, "Labels")
    assert not check("estate-gcp-atelier", out, "Does not ask")


def test_eval25_fails_no_versioning_and_fine_grained_access(tmp_path):
    code = GOOD.replace(
        "uniform_bucket_level_access = true", "uniform_bucket_level_access = false"
    ).replace("  versioning {\n    enabled = true\n  }\n", "")
    assert code.count("true") == 0
    out = run(tmp_path, "Done.", {"storage.tf": code, SCOPE: RECORDED}, repo=False)
    assert not check("estate-gcp-atelier", out, "Versioning")
    assert not check("estate-gcp-atelier", out, "Uniform")
