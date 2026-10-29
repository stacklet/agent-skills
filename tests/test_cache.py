import json
import os

import _cache
import pytest


@pytest.mark.skipif(os.name == "nt", reason="chmod can't restrict reading on Windows")
class TestMakeDir:
    def test_a_dir_it_may_not_tighten_is_still_usable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_cache, "CACHE_DIR", tmp_path)
        c = _cache.Cache("console.example.com")
        c.dir.mkdir()
        c.dir.chmod(0o755)

        def refuse(*a):
            raise PermissionError("not yours")

        monkeypatch.setattr(type(c.dir), "chmod", refuse)
        c.make_dir()


@pytest.mark.parametrize(
    ("src", "want"),
    [
        (json.dumps({"actions": ["delete"]}), ["delete"]),
        (
            json.dumps({"actions": [{"type": "tag", "key": "k"}, "notify"]}),
            ["tag", "notify"],
        ),
        (
            json.dumps({"actions": [{"type": "mark-for-op", "op": "stop", "days": 1}]}),
            ["mark-for-op:stop"],
        ),
        (json.dumps({"actions": [{"type": "mark-for-op"}]}), ["mark-for-op"]),
        (
            json.dumps({"actions": ["notify", {"type": "notify", "to": ["x"]}]}),
            ["notify"],
        ),
        (json.dumps({"policies": [{"actions": ["stop"]}]}), ["stop"]),
        (json.dumps({"filters": []}), []),
        ("not json", []),
        (None, []),
    ],
)
def test_action_names_follow_the_custodian_forms(src, want):
    assert _cache.action_names(src) == want
