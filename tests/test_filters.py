import json

import pytest

from _filters import disjoint, holds, nesting, region, render_filter


def batch(name):
    """A dynamicFilter like a real deployment's rollout batches: a tag value, minus an opt-out tag."""
    return json.dumps(
        {
            "multiple": {
                "operator": "AND",
                "operands": [
                    {"single": {"name": "tag:RolloutWave", "value": name}},
                    {
                        "multiple": {
                            "operator": "NOT",
                            "operands": [
                                {
                                    "single": {
                                        "name": "tag:Ignore",
                                        "operator": "exists",
                                    }
                                }
                            ],
                        }
                    },
                ],
            }
        }
    )


@pytest.mark.parametrize(
    ("f", "words"),
    [
        ("", "every account in the provider, present and future"),
        (None, "no filter (members added by hand)"),
        ("tag:team = payments", "legacy filter: tag:team = payments"),
        (
            batch("ProdAlphaWave"),
            "tag:RolloutWave equals ProdAlphaWave AND NOT (tag:Ignore exists)",
        ),
        (
            json.dumps(
                {"single": {"name": "org", "operator": "equals", "values": ["o-1"]}}
            ),
            "org equals o-1",
        ),
        (
            "org:o-1 tag:team~=pay path:/Apps/",
            "org equals o-1 AND tag:team~=pay AND path starts with /Apps/ (legacy filter)",
        ),
        ('{"what": 1}', 'unrecognised filter: {"what": 1}'),
        ('{"multiple": "x"}', 'unrecognised filter: {"multiple": "x"}'),
        ('{"single": [1]}', 'unrecognised filter: {"single": [1]}'),
        (
            '{"multiple": {"operator": "AND", "operands": []}}',
            'unrecognised filter: {"multiple": {"operator": "AND", "operands": []}}',
        ),
    ],
)
def test_filters_are_rendered_in_words_or_shown_as_written(f, words):
    assert render_filter(f) == words


def test_not_takes_exactly_one_operand():
    single = {"single": {"name": "tag:A", "operator": "exists"}}
    two = {"multiple": {"operator": "NOT", "operands": [single, single]}}
    assert render_filter(json.dumps(two)).startswith("unrecognised filter: ")


def test_a_filter_names_its_org_beside_its_key():
    single = json.dumps(
        {"single": {"name": "org", "operator": "equals", "values": ["o-1"]}}
    )
    assert render_filter(single, {"o-1": "Acme"}) == "org equals o-1 (Acme)"
    assert (
        render_filter("org:o-1 path:/A/", {"o-1": "Acme"})
        == "org equals o-1 (Acme) AND path starts with /A/ (legacy filter)"
    )


ORG = '{"single":{"name":"org","operator":"equals","values":["o-1"]}}'


def group(name, f, n):
    return {
        "uuid": name,
        "name": name,
        "provider": "AWS",
        "dynamic_filter": f,
        "member_count": n,
    }


def or_paths(org, *paths):
    """A JSON filter: ORG and any of PATHS."""
    return json.dumps(
        {
            "multiple": {
                "operator": "AND",
                "operands": [
                    {"single": {"name": "org", "operator": "equals", "value": org}},
                    {
                        "multiple": {
                            "operator": "OR",
                            "operands": [
                                {
                                    "single": {
                                        "name": "path",
                                        "operator": "starts with",
                                        "value": p,
                                    }
                                }
                                for p in paths
                            ],
                        }
                    },
                ],
            }
        }
    )


def and_(*singles):
    """A JSON filter: all of SINGLES, each (name, operator, value)."""
    return json.dumps(
        {
            "multiple": {
                "operator": "AND",
                "operands": [
                    {"single": {"name": n, "operator": op, "value": v}}
                    for n, op, v in singles
                ],
            }
        }
    )


def test_groups_nest_under_the_smallest_group_holding_their_accounts():
    groups = {
        g["uuid"]: g
        for g in (
            group("all", "", 100),
            group("org", ORG, 100),
            group("ou-a", "org:o-1 path:/A/", 60),
            group("ou-a-too", "org:o-1 path:/A/", 60),
            group("ou-a-tagged", "org:o-1 tag:x~=y path:/A/", 20),
            group("ou-a-or-b", or_paths("o-1", "/A/", "/B/"), 90),
            group("by-hand", None, 3),
            group("other-org", "org:o-2 path:/A/", 5),
        )
    }
    nest = nesting(groups)
    # never under the all-accounts group: every group is in it
    assert nest["org"] == (None, [])
    assert nest["ou-a-or-b"] == ("org", [])
    # two filters for the same OU hold the same accounts: neither is under the other
    assert nest["ou-a"] == ("ou-a-or-b", ["ou-a-too"])
    assert nest["ou-a-too"] == ("ou-a-or-b", ["ou-a"])
    # a tag narrows its OU: under the smallest of the groups holding it (ties by name)
    assert nest["ou-a-tagged"] == ("ou-a", [])
    # nothing says where these are
    assert nest["by-hand"] == (None, [])
    assert nest["other-org"] == (None, [])


@pytest.mark.parametrize(
    ("f", "read"),
    [
        ("", (None, None, False)),
        (None, None),
        (ORG, ("o-1", None, False)),
        ("org:o-1 path:/A/", ("o-1", ("/A/",), False)),
        ("org:o-1 tag:x~=y path:/A/", ("o-1", ("/A/",), True)),
        (or_paths("o-1", "/A/", "/B/"), ("o-1", ("/A/", "/B/"), False)),
        ("tag:x~=y", None),  # no org: nothing places it
        # what can't be read unambiguously isn't read (none of it is seen on real postures)
        ("org:o-1 path:/A/ path:/B/", None),  # two legacy paths: AND, or any of them?
        ("org:o-1 NOT path:/A/", None),
        ("org: path:/A/", None),
        (
            and_(
                ("org", "equals", "o-1"),
                ("path", "starts with", "/A/B/"),
                ("path", "starts with", "/A/"),
            ),
            None,
        ),
        (and_(("org", "equals", "o-1"), ("org", "equals", "o-2")), None),
        (json.dumps({"multiple": {"operator": "OR", "operands": []}}), None),
    ],
)
def test_a_filter_is_read_for_where_its_accounts_are_or_not_at_all(f, read):
    assert region(f) == read


@pytest.mark.parametrize(
    ("h", "g", "held"),
    [
        ("", "org:o-1 path:/A/", True),  # every account
        (ORG, "org:o-1 path:/A/", True),
        ("org:o-1 path:/A/", ORG, False),
        ("org:o-2", "org:o-1 path:/A/", False),
        ("org:o-1 path:/A/", "org:o-1 path:/A/B/", True),
        ("org:o-1 path:/A/", "org:o-1 tag:x~=y path:/A/", True),
        (
            "org:o-1 tag:x~=y path:/A/",
            "org:o-1 path:/A/",
            False,
        ),  # a tag narrows the holder
        (or_paths("o-1", "/A/", "/B/"), "org:o-1 path:/B/C/", True),
        ("org:o-1 path:/A/", or_paths("o-1", "/A/", "/B/"), False),
        # "starts with" is a string prefix: /A holds /A/ and /AB/; /A/ holds neither /A nor /AB/
        ("org:o-1 path:/A", "org:o-1 path:/A/", True),
        ("org:o-1 path:/A", "org:o-1 path:/AB/", True),
        ("org:o-1 path:/A/", "org:o-1 path:/A", False),
        (None, "org:o-1 path:/A/", False),
    ],
)
def test_which_groups_hold_which(h, g, held):
    assert holds(region(h), region(g)) is held


@pytest.mark.parametrize(
    ("a", "b", "apart"),
    [
        ("org:o-1 path:/A/", "org:o-1 path:/B/", True),
        ("org:o-1 path:/A/", "org:o-2 path:/A/", True),
        ("org:o-1 path:/A/", "org:o-1 path:/A/B/", False),
        ("org:o-1 path:/A", "org:o-1 path:/AB/", False),  # /A is a prefix of /AB/
        (ORG, "org:o-1 path:/A/", False),
        ("org:o-1 path:/A/", None, False),  # by hand: anywhere
    ],
)
def test_which_groups_cannot_overlap(a, b, apart):
    assert disjoint(region(a), region(b)) is apart
