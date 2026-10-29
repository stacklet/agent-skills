"""Reading an account group's dynamic filter: in words, and for which accounts it holds.

The digest (posture.py) shows each bound group's filter in words (render_filter), under the
smallest group holding all its accounts (nesting); the assessment's grader reads filters with these
too, so it grades by what the digest told the model.
"""

import json


def render_filter(dynamic_filter, org_names=None):
    """An account group's DYNAMIC_FILTER in words, each org key followed by its name from ORG_NAMES.
    JSON filters are trees of {"single": {name, operator, value(s)}} and {"multiple": {operator,
    operands}}; a legacy string's `org:K` and `path:P` terms are put in words too. Anything else is
    shown as written: a guess at its meaning would read as fact."""
    if dynamic_filter is None:
        return "no filter (members added by hand)"
    if dynamic_filter == "":
        return "every account in the provider, present and future"
    org_names = org_names or {}

    def org(key):
        name = org_names.get(key)
        return f"{key} ({name})" if name and name != key else key

    try:
        tree = json.loads(dynamic_filter)
    except ValueError:
        words = dynamic_filter.split()
        if not words or not all(":" in word for word in words):
            return f"legacy filter: {dynamic_filter}"
        terms = []
        for word in words:
            name, value = word.split(":", 1)
            if name == "org":
                terms.append(f"org equals {org(value)}")
            elif name == "path":
                terms.append(f"path starts with {value}")
            else:
                terms.append(word)
        return " AND ".join(terms) + " (legacy filter)"

    def walk(node):
        if not isinstance(node, dict):
            raise TypeError
        if "single" in node:
            single = node["single"]
            values = single.get("values") or (
                [single["value"]] if "value" in single else []
            )
            operator = single.get("operator") or "equals"
            if operator == "exists":
                return f"{single['name']} exists"
            if single["name"] == "org":
                values = [org(value) for value in values]
            return f"{single['name']} {operator} {', '.join(map(str, values))}"
        multiple = node["multiple"]
        operator = str(multiple.get("operator", "")).upper()
        parts = [walk(operand) for operand in multiple.get("operands") or []]
        if not parts:  # nothing to say: an empty AND reads as no condition at all
            raise ValueError
        if operator == "NOT":
            if len(parts) != 1:  # NOT takes exactly one operand
                raise ValueError
            return f"NOT ({parts[0]})"
        return parts[0] if len(parts) == 1 else "(" + f" {operator} ".join(parts) + ")"

    try:
        words = walk(tree)
    # AttributeError: a single or multiple that isn't an object
    except (AttributeError, KeyError, TypeError, ValueError):
        return f"unrecognised filter: {dynamic_filter}"
    return words[1:-1] if words.startswith("(") and words.endswith(")") else words


def region(dynamic_filter):
    """What DYNAMIC_FILTER says of every account it holds: (org or None, OU path prefixes or None
    for any, narrowed by other terms), or None if it can't be read so. "" is every account: (None,
    None, False). Only the unambiguous is read (not two orgs or paths, say): a wrong reading would
    tell a model one group holds another when it doesn't."""
    if dynamic_filter == "":
        return None, None, False
    try:
        tree = json.loads(dynamic_filter or "")
    except ValueError:
        # legacy: `org:K path:P tag:..`
        words = (dynamic_filter or "").split()
        if not all(":" in word for word in words):
            return None
        orgs = [word[4:] for word in words if word.startswith("org:")]
        paths = [word[5:] for word in words if word.startswith("path:")]
        if len(orgs) != 1 or not orgs[0] or len(paths) > 1 or "" in paths:
            return None
        return orgs[0], tuple(paths) or None, len(words) > 1 + len(paths)
    if not isinstance(tree, dict):
        return None
    if "single" in tree:
        terms = [tree]
    elif (
        isinstance(tree.get("multiple"), dict)
        and str(tree["multiple"].get("operator", "")).upper() == "AND"
    ):
        terms = tree["multiple"].get("operands") or []
    else:
        return None
    org, paths, narrowed = None, None, False

    def only_value(single):
        values = single.get("values") or (
            [single["value"]] if "value" in single else []
        )
        return values[0] if len(values) == 1 else None

    def path_of(term):
        single = term.get("single") if isinstance(term, dict) else None
        if (
            isinstance(single, dict)
            and single.get("name") == "path"
            and single.get("operator") == "starts with"
        ):
            return only_value(single)
        return None

    for term in terms:
        single = term.get("single") if isinstance(term, dict) else None
        multiple = term.get("multiple") if isinstance(term, dict) else None
        if (
            isinstance(single, dict)
            and single.get("name") == "org"
            and single.get("operator") == "equals"
        ):
            if org is not None:
                return None  # two orgs
            org = only_value(single)
        elif path_of(term) or (
            isinstance(multiple, dict)
            and str(multiple.get("operator", "")).upper() == "OR"
            and all(path_of(operand) for operand in multiple.get("operands") or [None])
        ):
            if paths is not None:
                return None  # two path terms
            paths = (
                (path_of(term),)
                if path_of(term)
                else tuple(path_of(operand) for operand in multiple["operands"])
            )
        else:
            narrowed = True
    if org is None:
        return None
    return org, paths, narrowed


def disjoint(one, other):
    """Whether no account can be in groups of both regions ONE and OTHER (regions as region() gives
    them): different orgs, or one org's OU paths neither of which holds the other."""
    if one is None or other is None:
        return False
    (one_org, one_paths, _), (other_org, other_paths, _) = one, other
    if one_org is None or other_org is None:
        return False
    if one_org != other_org:
        return True
    if one_paths is None or other_paths is None:
        return False
    # "starts with" is a string prefix: /A and /AB/ may share accounts (real paths lack the slash)
    return not any(
        mine.startswith(theirs) or theirs.startswith(mine)
        for mine in one_paths
        for theirs in other_paths
    )


def holds(holder, held):
    """Whether every account in a group of region HELD is in one of region HOLDER (regions as
    region() gives them)."""
    if holder is None or held is None:
        return False
    (holder_org, holder_paths, holder_narrowed), (held_org, held_paths, _) = (
        holder,
        held,
    )
    if holder_org is None and holder_paths is None and not holder_narrowed:
        return True  # every account
    if holder_narrowed or held_org is None or holder_org != held_org:
        return False
    if holder_paths is None:
        return True
    # a string prefix: /A holds /A/ (and /AB/), /A/ doesn't hold /A
    return held_paths is not None and all(
        any(path.startswith(prefix) for prefix in holder_paths) for path in held_paths
    )


def nesting(groups):
    """{group uuid: (parent uuid or None, [uuids of groups holding the same accounts])} for GROUPS
    ({uuid: row}): each under the smallest other group of its provider whose filter holds all its
    accounts (by org, OU path), never under an all-accounts group. A filter that doesn't say
    (hand-picked, tag-only, OR'd) leaves a group at the top."""
    regions = {uuid: region(group["dynamic_filter"]) for uuid, group in groups.items()}
    out = {}
    for uuid, group in groups.items():
        holders = [
            other
            for other, candidate in groups.items()
            if other != uuid
            and candidate["provider"] == group["provider"]
            and candidate["dynamic_filter"] != ""
            and holds(regions[other], regions[uuid])
        ]
        same = sorted(
            other for other in holders if holds(regions[uuid], regions[other])
        )
        strict = [other for other in holders if other not in same]
        parent = min(
            strict,
            key=lambda other: (
                groups[other]["member_count"],
                groups[other]["name"],
                other,
            ),
            default=None,
        )
        out[uuid] = (parent, same)
    return out
