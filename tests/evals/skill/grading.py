"""What the evals' checks are made of: reading a run's outputs and its scope file, diffing the
brownfield fixture, finding claims in a reply, the estate fixture's tags, and reading the
transcript.

Each eval's checks.py imports what it needs from here; grade_skill.py runs them.
"""

import json
import re

from pathlib import Path

from make_estate import ENVIRONMENTS
from make_fixture import cid


def read_all(d, suffixes):
    return "\n".join(
        p.read_text(errors="ignore")
        for p in sorted(d.rglob("*"))
        if p.is_file() and p.suffix in suffixes
    )


def final(d):
    """What the agent told the user: every text of the main agent's in the run's transcript, in
    order, not just its last message (a run_skill.py run's final_response.md). With a background
    sync, the last is often a short follow-up to an answer given while the sync ran. Without a
    transcript (a control), final_response.md."""
    texts = [
        c["text"]
        for e in transcript(d)
        if not e.get("parent_tool_use_id")
        for c in e.get("message", {}).get("content") or []
        if isinstance(c, dict) and c.get("type") == "text"
    ]
    if texts:
        return "\n\n".join(texts)
    f = d / "final_response.md"
    return f.read_text(errors="ignore") if f.exists() else ""


def last_reply(d):
    """The agent's last message (final_response.md): what reply_words and out_of_scope_sentences
    measure, as they always have, so their figures stay comparable; the checks read final()."""
    f = d / "final_response.md"
    return f.read_text(errors="ignore") if f.exists() else ""


def transcript(d):
    """The assistant entries of the transcript beside outputs dir D, in order ([] without one)."""
    path = d.parent / "transcript.jsonl"
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(errors="ignore").splitlines():
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if e.get("type") == "assistant":
            out.append(e)
    return out


def tf(d):
    """The code the agent wrote, not the scope file (.stacklet/ is JSON too)."""
    return "\n".join(
        p.read_text(errors="ignore")
        for p in sorted(d.rglob("*"))
        if p.is_file()
        and p.suffix in {".tf", ".json", ".py"}
        and ".stacklet" not in p.parts
    )


def nocomments(t):
    return re.sub(r"(?m)^\s*(#|//).*$|\s#.*$", "", t)


def ingress_open_world(t):
    """True if any *ingress* rule allows 0.0.0.0/0 (egress rules are ignored)."""
    t = nocomments(t)
    for m in re.finditer(r"ingress\s*\{[^}]*\}", t):
        if "0.0.0.0/0" in m.group(0):
            return True
    for m in re.finditer(
        r'resource\s+"aws_(?:vpc_security_group|security_group)_ingress_rule"[^{]*\{.*?(?=\nresource |\Z)',
        t,
        re.DOTALL,
    ):
        if "0.0.0.0/0" in m.group(0):
            return True
    return False


def has(rx, s, fl=re.IGNORECASE | re.DOTALL):
    return re.search(rx, s, fl) is not None


def numbers_set(attr, t):
    """The numbers ATTR is set to in T: literals, and the defaults of variables it is set from
    (agents often lift a guardrail's value into a variable with a documented default)."""
    vals = [int(x) for x in re.findall(attr + r"\s*=\s*(\d+)", t)]
    for var in re.findall(attr + r"\s*=\s*var\.(\w+)", t):
        vals += [
            int(x)
            for x in re.findall(r'variable\s+"' + var + r'"[^}]*default\s*=\s*(\d+)', t)
        ]
    return vals


# the fixtures are shared with the unit tests
FIXTURES = Path(__file__).resolve().parents[2] / "fixture"
ORDERS_REPO = FIXTURES / "orders-repo"


# top-level blocks and the key each is known by: resources as "kind.label", the rest prefixed
# ("provider.aws", "variable.x", "data.kind.label", "locals"), so a change to the provider's
# default_tags or a local counts as much as one to a resource
BLOCK = re.compile(
    r"^(?:(resource)|(data)|(provider|variable|output|module)|(locals|terraform))"
    r'(?:\s+"([\w-]+)")?(?:\s+"([\w-]+)")?\s*\{'
)


def block_key(m):
    res, data, named, bare, a, b = m.groups()
    if res:
        return f"{a}.{b}"
    if data:
        return f"data.{a}.{b}"
    if named:
        return f"{named}.{a}"
    return bare


def is_resource(key):
    return "." in key and not key.startswith(
        ("data.", "provider.", "variable.", "output.", "module.")
    )


def resources(texts):
    """{key: set of normalised body lines} for the top-level blocks in TEXTS (see BLOCK).
    Comments, blank lines and alignment are ignored, so `terraform fmt` re-aligning a block
    doesn't count as changing it."""
    res, cur = {}, None
    for text in texts:
        for ln in text.splitlines():
            m = BLOCK.match(ln)
            if m:
                cur = res.setdefault(block_key(m), set())
                if ln.rstrip().endswith("}"):  # a one-line block: `resource "a" "b" {}`
                    cur = None
            elif cur is not None and ln.rstrip() == "}":
                cur = None
            elif cur is not None:
                ln = re.sub(r"\s(#|//).*$", "", ln).strip()
                if ln and not ln.startswith(("#", "//")):
                    cur.add(re.sub(r"\s+", " ", re.sub(r"\s*=\s*", " = ", ln)))
    return res


def tf_blocks(d):
    """resources() of the Terraform files in directory D."""
    return resources(p.read_text(errors="ignore") for p in sorted(d.glob("*.tf")))


# Adding the required tags with the values the repo already holds (local.finops_tags) is a free fix
# SKILL.md allows on a resource the change edits (not on its neighbours); an invented value is
# still an unexpected change.
FREE_TAG_LINE = (
    r"^(tags = (merge\(local\.finops_tags, )?\{|tags = local\.finops_tags|\}\)?,?|"
    r'"?cost-center"? = ("cc-4411"|local\.finops_tags(\["cost-center"\]|\.cost-center))'
    r'|"?data-classification"? = ("internal"|local\.finops_tags(\["data-classification"\]|\.data-classification))'
    r'|"?team"? = "orders")$'
)


FREE_TAG_FRAME = r"^(tags = \{|\}\)?,?)$"


def unexpected_changes(d, allowed=None, touched=(), may_remove=(), new=None):
    """Changes to the fixture's existing blocks other than ALLOWED ({key: regex of lines that may
    change}): a brownfield edit should leave everything it wasn't asked about alone, provider and
    locals included (default_tags would retag every resource). TOUCHED names the resources the
    change edits, the only ones that may gain the free tags; MAY_REMOVE those that may vanish (a
    replacement under a new label); NEW is a regex of resource kinds the change may add (None:
    none; data sources, variables and outputs may always be added, and locals may grow).
    Returns "key: line" strings; a block that vanished or a resource added is reported whole."""
    allowed = allowed or {}
    before, after = tf_blocks(ORDERS_REPO), tf_blocks(d)
    bad = []
    for key, body in before.items():
        if key not in after:
            if key not in may_remove:
                bad.append(key + ": removed")
            continue
        added = set() if key == "locals" else after[key] - body
        for ln in sorted(body - after[key]):
            # only the frame of a tags block may go (rewritten as merge(local.finops_tags, {...})):
            # a removed tag, `team` say, is a decision someone made, undone
            if key in touched and re.search(FREE_TAG_FRAME, ln):
                continue
            if not (key in allowed and re.search(allowed[key], ln)):
                bad.append(key + ": " + ln)
        for ln in sorted(added):
            if key in touched and re.search(FREE_TAG_LINE, ln):
                continue
            if not (key in allowed and re.search(allowed[key], ln)):
                bad.append(key + ": " + ln)
    for key in sorted(set(after) - set(before)):
        # a module's resources are out of sight, so all of it
        if (
            key.startswith("module.")
            or is_resource(key)
            and not (new and re.fullmatch(new, key.split(".")[0]))
        ):
            bad.append(key + ": added")
    return bad


def copied_classification(d, key):
    """Resource KEY gained a data-classification from the repo (a literal, or by merging the
    repo's local.finops_tags), a judgement about the data."""
    before = tf_blocks(ORDERS_REPO).get(key, set())
    after = tf_blocks(d).get(key, set())
    return any(
        has(
            r"data-classification|merge\(local\.finops_tags|tags = local\.finops_tags",
            ln,
        )
        for ln in after - before
    )


def added_text(d):
    """The .tf lines D has that the fixture doesn't: what the agent wrote, so checks on new code
    aren't satisfied by the fixture's own text (its locals already hold valid tag values)."""
    before = []
    for p in ORDERS_REPO.glob("*.tf"):
        before += [ln.strip() for ln in p.read_text().splitlines()]
    out = []
    for p in sorted(d.glob("*.tf")):
        for ln in p.read_text(errors="ignore").splitlines():
            if ln.strip() in before:
                before.remove(ln.strip())
            else:
                out.append(ln)
    return "\n".join(out)


def blocks(kind, t):
    """{label: body} for every resource of KIND in T."""
    return {
        m.group(1): block(kind, m.group(1), t)
        for m in re.finditer(r'resource\s+"' + kind + r'"\s+"([\w-]+)"', t)
    }


def known_tags_ok(b):
    """B sets no required tag to a value the repo doesn't already hold: either both via
    local.finops_tags, or literally cc-4411 / internal (or neither tag at all)."""
    if has(r"local\.finops_tags", b):
        return True
    cc = re.search(r'"?cost-center"?\s*=\s*"([^"]*)"', b)
    dc = re.search(r'"?data-classification"?\s*=\s*"([^"]*)"', b)
    return (not cc or cc.group(1) == "cc-4411") and (
        not dc or dc.group(1) == "internal"
    )


def block(kind, label, t):
    """The body of resource KIND.LABEL in T (up to the next top-level block), or ''."""
    m = re.search(
        r'resource\s+"'
        + kind
        + r'"\s+"'
        + label
        + r'"\s*\{.*?(?=\n(?:resource|data|module|variable|output|locals)\b|\Z)',
        t,
        re.DOTALL,
    )
    return m.group(0) if m else ""


def sentences(f):
    """F's prose split into sentences (and lines), code blocks left out."""
    return [
        x
        for x in re.split(r"(?<=[.!?])\s+|\n+", "\n".join(prose_lines(f)))
        if x.strip()
    ]


# What the agent says it didn't do ("I did not add a DLQ", "encryption not touched") is no claim
# about the resource, though GAP's "no"/"not" would read it as one: such a clause is taken out, up
# to where a reason may start ("I didn't add one, since the queue has no redrive policy" keeps that).
UNTIL = r"[^.;,:\n]*?(?=\b(?:because|since|as|so|but|which|while)\b|[.;,:\n]|$)"
OWN_NEGATION = re.compile(
    r"\b(?:I|we)(?:'ve|'d|'ll|'m)?(?:\s+(?:have|had|will|would|did|do|am|was|deliberately|"
    r"intentionally|also))*\s*(?:not|n't|never)\b"
    + UNTIL
    + r"|\b(?:(?:did|do|does|have|has|had|was|were|is|are|wo|will)\s*(?:not|n't)|never)\s+(?:been\s+)?"
    r"(?:add|chang|touch|set|enabl|fix|creat|turn|modif|include|including|alter)\w*"
    + UNTIL
    + r"|\bnot\s+(?:touched|changed|modified|added|altered|enabled)\b",
    re.IGNORECASE,
)


def claims(f):
    """F's prose_lines with the agent's own undone actions taken out (OWN_NEGATION), for checks
    that the reply says something is wrong with an existing resource (said_together's WITHIN)."""
    return [OWN_NEGATION.sub("", x) for x in prose_lines(f)]


def said_together(f, *rxs, within=sentences):
    """Some sentence of F (or paragraph, with WITHIN=prose_lines) matches every regex in RXS:
    a claim, not keywords scattered through the reply."""
    return any(all(has(rx, x) for rx in rxs) for x in within(f))


# a customer-managed key, and what it costs whatever reads or writes the resource
KEY = r"\bkeys?\b|KMS|CMK"
KEY_COST = r"kms:|key polic|permission|grant|\baccess\b|reader"
# a dead-letter queue, and what adding one changes
DLQ = r"dead.?letter|\bDLQ\b|redrive"
DLQ_COST = r"flag|retr(y|ies)|fail|poison|behaviour|behavior|handled|depend"
# saying something is missing or wrong now
GAP = r"\bno\b|lack|without|missing|isn.?t|doesn.?t|\bnot\b|rather than|instead|SQS-managed|SQS.s own|AWS[- ]managed|built.in|S3-managed|AES256|SSE-S3|break|violat|flag|fail"


def drift_sentences(f):
    """How many sentences of F are about the public docs bucket's drift: a change that doesn't touch
    that bucket should mention it in a line, not explain it again."""
    return sum(
        has(r"public.?docs|set-public-block|s3-block-public-access", x)
        for x in re.split(r"(?<=[.!?])\s+|\n+", f)
    )


LIST_ITEM = re.compile(r"(\s*)(?:[-*+]|\d+[.)])\s")


def prose_lines(f):
    """F's lines outside fenced code blocks. A list item carries the lines that introduce it (ones
    ending in a colon: "Still missing on the queue:", at any outer level), so an item can complete
    a claim its heading starts."""
    out, fenced, leads = [], False, []  # leads: (indent, text) of the colon lines above
    for ln in f.splitlines():
        if ln.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        intro = ln.strip().rstrip("*_ ").endswith(":")
        m = LIST_ITEM.match(ln)
        if m:
            indent = len(m.group(1))
            leads = [x for x in leads if x[0] < indent]
            out.append(" ".join([x[1] for x in leads] + [ln.strip()]))
            if intro:
                leads.append((indent, ln.strip()))
        else:
            out.append(ln)
            if intro:
                leads = [(-1, ln.strip())]
            elif ln.strip():
                leads = []
    return out


def classification_said(d, key, f):
    """Resource KEY didn't gain a data-classification from the repo (copied_classification), or F
    says it did: the repo's default may be wrong for this data, so SKILL.md has the agent say so
    when it sets one."""
    return not copied_classification(d, key) or said_together(
        f,
        r"classif|label\w* the data|sensitive|sensitivity",
        r"\binternal\b|finops|repo|default|local",
        within=prose_lines,
    )


PCI_UUID = cid("PCI-DSS AWS")


def scope_file(path):
    """The scope file at PATH, parsed, or None if there is none or it isn't a JSON object."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def scope_decisions(path):
    """{collection uuid: include/exclude} recorded in a scope file, or None if unreadable."""
    state = scope_file(path)
    try:
        return {d["collection"]: d["decision"] for d in state.get("decisions", [])}
    # no file, or not of a scope file's shape
    except (KeyError, TypeError, AttributeError):
        return None


# the platform or its policies as the subject of a claim (eval 3, 20)
PLATFORM = r"platform|polic(y|ies)|guardrail|\bCIS\b|stacklet|custodian"


def mentions_guardrails(s):
    # not "policy": evals 1 and 3 ask for a bucket policy, so it would always match
    return has(r"guardrail|stacklet|\bCIS\b|\bacme\b|ACME-\w+", s)


TAGS_OK = lambda t: (
    has(r"cost-center", t)
    and has(r"data-classification", t)
    and (
        has(
            r"data-classification\"?\s*=\s*\"(public|internal|confidential|restricted)\"",
            t,
        )
        or (
            # any variable feeding the tag, e.g. var.ledger_data_classification
            has(r"variable\s+\"\w*data_classification\"", t)
            and has(
                r"(default|contains)[^\n]*(public|internal|confidential|restricted)", t
            )
        )
    )
)


# What the agent did, from the run's transcript (run_skill.py keeps it beside outputs/).

SCRIPT = r"guardrails/scripts/\w+\.py"


def tool_calls(d, sub=False):
    """[(tool name, its input)] in order from the transcript beside outputs dir D, or None: the main
    agent's, or with SUB, a sub-agent's too (a background sync's), as they happened. Its checks are
    about what the agent did, so a sync's calls aren't counted as its own."""
    if not (d.parent / "transcript.jsonl").exists():
        return None
    return [
        (c.get("name"), c.get("input") or {})
        for e in transcript(d)
        if sub or not e.get("parent_tool_use_id")
        for c in e.get("message", {}).get("content") or []
        if isinstance(c, dict) and c.get("type") == "tool_use"
    ]


def commands(calls, rx):
    """Indexes of the Bash calls whose command matches RX."""
    return [
        i
        for i, (name, inp) in enumerate(calls)
        if name == "Bash" and has(rx, str(inp.get("command", "")))
    ]


# what a shell command writes to: `cat > main.tf <<EOF`, `>> x`, `tee [-a] x`
BASH_WRITE = re.compile(r"""(?:>>?|\btee(?:\s+-a)?)\s*['"]?([^\s'";&|<>]+)""")


def in_repo(path):
    """Whether PATH is in the repo: run_skill.py's outputs dir, or relative. Not a temp dir or a
    shell variable's (`$D/posture.json`: a sync writing the responses it fetches)."""
    return "/outputs/" in path or not path.startswith(("/", "~", "$"))


def written(name, inp):
    """(the file a call writes, the text it writes) for an edit, or a shell command writing a
    Terraform file (heredocs, which edits alone miss; the edit-timing checks are about resources),
    else None. Of a command's redirects, the first into the repo, if any."""
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = str(inp.get("file_path") or inp.get("notebook_path") or "")
        text = inp.get("content") or inp.get("new_string") or inp.get("edits") or ""
        return path, str(text)
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        targets = [
            m.group(1) for m in BASH_WRITE.finditer(cmd) if m.group(1).endswith(".tf")
        ]
        if targets:
            return next((t for t in targets if in_repo(t)), targets[0]), cmd
    return None


def first_edit(calls):
    """Index of the first edit to the repo's code (not the reply file), or None. Only in the repo
    (run_skill.py's outputs dir, or a relative path): a sync writes the responses it fetches to a
    temp dir, the agent's own too when it syncs itself. Nor a new Terraform file defining no
    resource (provider pins, variables, outputs): guardrails are about resources, and SKILL.md
    holds back only code defining them while it waits."""
    for i, (name, inp) in enumerate(calls):
        w = written(name, inp)
        if w is None:
            continue
        path, text = w
        no_resource = (
            name in ("Write", "Bash")
            and path.endswith(".tf")
            and not has(r'^\s*(resource|module)\s+"', text, re.MULTILINE)
        )
        if in_repo(path) and not no_resource and not path.endswith("final_response.md"):
            return i
    return None


def chained_script_calls(calls):
    """Skill-script commands chained with others (SKILL.md: one plain command each, so a single
    permission rule covers them): measured, not graded."""
    return len(
        commands(
            calls, SCRIPT + r".*(&&|\|\||;|\|)|(&&|\|\||;)\s*python3? \S*" + SCRIPT
        )
    )


# The estate fixture (make_estate.py): its required tags, and its environment values.
MRD_ENVIRONMENTS = ENVIRONMENTS

# The estate's trial remediation (its collection, its group, its policies' namespace): it reaches
# only the one account in that group.
TRIAL_REMEDIATION = r"Live Remediation|Trial Remediation|remediation trial|\btrial:"
# The estate evals' OUT_OF_SCOPE (grade_skill.py): guardrails a reply has no reason to mention, for the
# wave-rolled AWS accounts (evals 22-24) and for the trial remediation account's queues (26-28).
OUT_OF_SCOPE_WAVES = TRIAL_REMEDIATION + r"|atelier"
OUT_OF_SCOPE_TRIAL = r"atelier|valkey|graviton"

# The estate's GuardrailPause opt-out, however a reply words it (evals 22, 24). Not "exempt": a
# reply says what the opt-out does with that word, so it belongs with what is said of it.
OPT_OUT = r"GuardrailPause|paus|opt.?(?:ed)?.?out"
# ... and however a question words it: "accounts exempt from the rollout" names the opt-out too
OPT_OUT_ASKED = OPT_OUT + r"|exempt"
# a negation just before a word ("not excluded", "nothing is excluded", "isn't exempt")
NEGATED_BEFORE = re.compile(
    r"(?:\b(?:not|never|nothing|none|no)|n't)(?:\s+\w+){0,2}\s*$", re.IGNORECASE
)


def unnegated(rx, s):
    """Some match of RX in S that no negation just before it denies."""
    return any(
        not NEGATED_BEFORE.search(s[: m.start()])
        for m in re.finditer(rx, s, re.IGNORECASE)
    )


def variable_body(name, t):
    """The block of variable NAME in T (up to the next top-level block), or ''."""
    m = re.search(
        rf'variable\s+"{name}"\s*\{{.*?(?=\n(?:variable|resource|locals|output|data)\b|\Z)',
        t,
        re.DOTALL,
    )
    return m.group(0) if m else ""


def mrd_tags_ok(t, defs=None):
    """T sets the estate's three required tags, mrd:environment to dev, stage or prod: literally, or
    from a variable whose default is one of them or whose validation allows only them, or from a
    local set to one. Only that value counts, not the words anywhere in T. A variable or local is
    looked for in DEFS (default T): where T is just the tags a resource takes."""
    defs = t if defs is None else defs
    if not all(
        has(rf'"?mrd:{k}"?\s*=', t) for k in ("owner", "service", "environment")
    ):
        return False
    one = rf'"({"|".join(MRD_ENVIRONMENTS)})"'
    # not the `,` or `}` after it: a one-line map is `{ "mrd:environment" = "prod", ... }`
    m = re.search(r'"?mrd:environment"?\s*=\s*([^\s,}]+)', t)
    value = m.group(1) if m else ""
    if re.fullmatch(one, value):
        return True
    ref = re.fullmatch(r"(var|local)\.(\w+)", value)
    if not ref:
        return False
    kind, name = ref.groups()
    if kind == "local":
        return has(rf"\b{name}\s*=\s*{one}", defs)
    body = variable_body(name, defs)
    allows = re.search(r"contains\(\s*\[([^\]]*)\]", body)
    return has(rf"default\s*=\s*{one}", body) or bool(
        allows and set(re.findall(r'"(\w+)"', allows.group(1))) <= set(MRD_ENVIRONMENTS)
    )


def scope_answers(path):
    """[(question text, answer)] recorded in a scope file, or None if unreadable. By text, not id:
    question ids are the assessing model's, and differ between assessments."""
    state = scope_file(path)
    try:
        return [(a.get("name") or "", a["answer"]) for a in state.get("answers", [])]
    except (KeyError, TypeError, AttributeError):
        return None


# The estate's trial remediation deletes resources without an Owner tag (evals 26, 27).
# Its tag key exactly: keys are case-sensitive, and "mrd:owner" is another tag.
OWNER = r"(?<![\w:-])(?-i:Owner)\b"


def owner(s):
    return has(OWNER + r'"?\s*=', s)


def default_tags(t):
    """The provider's default_tags block: they go on every resource it creates."""
    m = re.search(r"default_tags\s*\{.*?\n\s*\}\s*\n", t, re.DOTALL)
    return m.group(0) if m else ""


def shared_tags(b, t):
    """The body of the local or variable a resource block B takes its tags from (`tags =
    local.x`, `merge(local.x, ..)`, `var.x`) in T, or ''."""
    m = re.search(r"tags\s*=\s*(?:merge\(\s*)?(local|var)\.(\w+)", b)
    if not m:
        return ""
    kind, name = m.groups()
    if kind == "local":
        # inside a locals block: a resource's own `tags = {..}` shares the name a local often has
        locals_ = "\n".join(
            m.group(0) for m in re.finditer(r"(?m)^locals\s*\{.*?^\}", t, re.DOTALL)
        )
        # a map, or a merge() of one with others (`tags = merge(var.extra, { .. })`)
        found = re.search(
            rf"\b{name}\s*=\s*(?:merge\([^{{]*)?\{{.*?\n\s*\}}", locals_, re.DOTALL
        )
        return found.group(0) if found else ""
    return variable_body(name, t)


def every_queue_owned(t):
    """Every SQS queue in T has an Owner tag: directly, through the local or variable its tags come
    from, or through the provider's default_tags. Only where its tags come from counts, not an
    Owner anywhere in T (another resource's, say)."""
    queues = blocks("aws_sqs_queue", t)
    return bool(queues) and (
        owner(default_tags(t))
        or all(owner(b) or owner(shared_tags(b, t)) for b in queues.values())
    )


def new_blocks(kind, t, repo):
    """{label: body} for the resources of KIND in T that fixture REPO doesn't define: what the
    agent added, so checks on new code aren't satisfied by the fixture's own resources."""
    before = blocks(kind, "\n".join(p.read_text() for p in repo.glob("*.tf")))
    return {k: b for k, b in blocks(kind, t).items() if k not in before}


def tags_of(b, t):
    """The tags resource block B takes in T: its own, the local or variable they come from, and the
    provider's default_tags; no other resource's."""
    return "\n".join((b, shared_tags(b, t), default_tags(t)))


def recorded_accounts(scope):
    """The account keys a scope file records, or None if there is none: the keys, not the text (a
    reason may name an account it explains leaving out)."""
    state = scope_file(scope)
    try:
        return {x["key"] for x in state.get("accounts", [])}
    except (KeyError, TypeError, AttributeError):
        return None


def recorded_account_check(calls, arm, what="account"):
    """process_checks for an eval whose agent should record the account (a GCP project: WHAT) it
    found; none without the skill, which has no scope.py."""
    if arm == "without_skill":
        return []
    return [
        (
            f"Recorded the {what} with scope.py decide --account",
            # --account itself, not --forget-account
            bool(commands(calls, r"scope\.py\s+\S+\s+decide\b.*(?<![\w-])--account\b")),
        )
    ]


def looked_up(calls, rt):
    """Indexes of the calls that looked resource type RT up with `distill.py show`: named, or one of
    the types a shell loop runs it over (`for t in aws.ec2 aws.ebs; do .. show "$t"`)."""
    named = r"distill\.py\s+\S+\s+show\s+(--\S+\s+)*" + re.escape(rt) + r"\b"
    looped = (
        r"\bfor\s+\w+\s+in\b[^;]*?(?<![\w.-])"
        + re.escape(rt)
        + r"(?![\w.-])[^;]*;.*distill\.py\s+\S+\s+show\b"
    )
    return sorted(set(commands(calls, named)) | set(commands(calls, looped)))


# The flow evals' fake Stacklet server's log (grade_skill.server_log: what was fetched, in order),
# the cache the run left (grade_skill.cache_state: what was ingested), and every tool call, a
# background sync's included (tool_calls(sub=True): when).
def fetches(log, kind):
    """The logged calls of KIND (posture, collections, account, deployment)."""
    return [x for x in log if x.get("kind") == kind]


def times_fetched(log, name):
    """How many collection queries asked for the collection NAME."""
    u = cid(name)
    return sum(u in x.get("collections", []) for x in fetches(log, "collections"))


def refetched(log):
    """Collections asked for more than once: a sync fetches each once."""
    seen = {}
    for x in fetches(log, "collections"):
        for u in x.get("collections", []):
            seen[u] = seen.get(u, 0) + 1
    return {u for u, n in seen.items() if n > 1}


def first_index(log, test):
    """Index of the first log entry TEST passes, or None."""
    return next((i for i, x in enumerate(log) if test(x)), None)


def before(log, first, then):
    """Whether the first entry passing FIRST precedes the first passing THEN (and both occur)."""
    a, b = first_index(log, first), first_index(log, then)
    return a is not None and b is not None and a < b


def names(name):
    """A log test: a collections query naming collection NAME."""
    return lambda x: cid(name) in x.get("collections", [])


def is_kind(kind):
    return lambda x: x.get("kind") == kind


def loaded(state, name):
    """Whether the run's cache holds collection NAME's membership, fetched under its current
    posture: ingested, not just fetched."""
    m = state.get("memberships", {}).get(cid(name))
    return m is not None and m == state.get("refresh_id")


def all_exact(log):
    """Every GraphQL query was the skill's own (fake_stacklet_mcp.is_skills): one that failed (bad
    variables, say) or isn't recognised is not."""
    queries = [
        x
        for x in log
        if x.get("tool", "platform_graphql_query") == "platform_graphql_query"
    ]
    return bool(queries) and all(x.get("exact") is True for x in queries)


def ingest_calls(calls, what):
    """Indexes of the calls running `ingest.py ... WHAT` (posture, collections, accounts)."""
    return commands(calls, rf"ingest\.py\s+\S+\s+{what}\b")


def written_after(calls, what, rx, since=None):
    """Whether the first edit defining RX (a resource's regex) comes after some `ingest.py WHAT`,
    or with SINCE, after one that follows the first `ingest.py SINCE` (collections a lookup
    brought in, not ones fetched before it)."""
    edit = next(
        (
            i
            for i, (name, inp) in enumerate(calls)
            if (w := written(name, inp)) and has(rx, w[1])
        ),
        None,
    )
    ingests = ingest_calls(calls, what)
    if since is not None:
        first = ingest_calls(calls, since)
        ingests = [i for i in ingests if first and i > min(first)]
    return edit is not None and bool(ingests) and min(ingests) < edit
