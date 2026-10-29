"""Generate a small synthetic posture (posture.json + collections.json) for evals, and two account
lookups (account_0.json, account_1.json).

usage: make_fixture.py OUTDIR
       make_fixture.py --stamp POSTURE_DB FILE [FILE ..]
Then, with STACKLET_GUARDRAILS_CACHE=CACHEDIR: <skill dir>/scripts/ingest.py console.fixture.example
posture OUTDIR/posture.json, make_fixture.py --stamp CACHEDIR/console.fixture.example/posture.db
OUTDIR/collections.json, ingest.py console.fixture.example collections OUTDIR/collections.json, and
(eval 11) ingest.py console.fixture.example accounts OUTDIR/account_0.json. --stamp marks a
collections response as fetched under the posture just ingested, as scope.py query's queries
do (ingest refuses one without it); the refresh id is made at ingest, so the fixture can't
carry it. A response stamped already is left alone, a stale stamp included: make the fixture afresh
after another posture ingest.

Invented data, shaped like the real responses (see the skill's platform/collection.graphql). Layout, and how a model
assesses it (applies / ask / ignore):
  CIS AWS Baseline   bound to "All aws accounts" (dynamicFilter "")  -> applies
  PCI-DSS AWS        bound to "aws-payments" (3 of 40 accounts)      -> ask (a payments question)
  Sandbox Tagging    bound to a 1-account group                      -> ignore
  Acme Org Standards bound to "All aws accounts"                     -> applies (unguessable org rules)
Account 111111111111 (ledger-prod, account_0.json) is in "All aws accounts" and "aws-payments", so
PCI-DSS AWS reaches it. Account 222222222222 (jdoe-sandbox, account_1.json) is the sandbox group's
one account, so only its lookup brings Sandbox Tagging in. One policy acts
(cis:s3-block-public-access: set-public-block); the rest only report.
"""

import json
import re
import sqlite3
import sys
import uuid

from pathlib import Path


def u(name):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name))


def cid(name):
    """The uuid of the collection NAME. The tests, the eval runner and graders import these, rather
    than repeating how the fixture makes them."""
    return u("c:" + name)


def gid(name):
    """The uuid of the account group NAME."""
    return u("g:" + name)


def policy(
    ns,
    name,
    resource,
    severity,
    compliance,
    description,
    filters,
    mode="pull",
    actions=None,
):
    full = f"{ns}:{name}"
    src = {
        "name": full,
        "resource": resource,
        "description": description,
        "filters": filters,
    }
    if actions:
        src["actions"] = actions
    return {
        "policy_uuid": u(full),
        "version": 1,
        "name": full,
        "provider": "AWS",
        "resource_type": resource,
        "mode": mode,
        "severity": severity,
        "compliance": compliance,
        "source_json": json.dumps(src),
    }


CIS = [
    policy(
        "cis",
        "s3-block-public-access",
        "aws.s3",
        "HIGH",
        ["CIS"],
        "S3 buckets must block public access",
        [{"type": "check-public-block", "BlockPublicAcls": False}],
        # the platform puts the block back: what eval 3's agent should warn about
        actions=[{"type": "set-public-block"}],
    ),
    policy(
        "cis",
        "s3-encryption-at-rest",
        "aws.s3",
        "HIGH",
        ["CIS"],
        "S3 buckets must use server-side encryption",
        [{"type": "bucket-encryption", "state": False}],
    ),
    policy(
        "cis",
        "ebs-encrypted",
        "aws.ebs",
        "HIGH",
        ["CIS"],
        "EBS volumes must be encrypted",
        [{"Encrypted": False}],
    ),
    policy(
        "cis",
        "rds-not-public",
        "aws.rds",
        "CRITICAL",
        ["CIS"],
        "RDS instances must not be publicly accessible",
        [{"PubliclyAccessible": True}],
    ),
    policy(
        "cis",
        "rds-encrypted",
        "aws.rds",
        "HIGH",
        ["CIS"],
        "RDS storage must be encrypted",
        [{"StorageEncrypted": False}],
    ),
    policy(
        "cis",
        "sg-no-open-ssh",
        "aws.security-group",
        "CRITICAL",
        ["CIS"],
        "Security groups must not allow 0.0.0.0/0 on port 22",
        [{"type": "ingress", "Ports": [22], "Cidr": "0.0.0.0/0"}],
    ),
]
PCI = [
    policy(
        "pci",
        "rds-backup-retention-35d",
        "aws.rds",
        "MEDIUM",
        ["PCI-DSS"],
        "Cardholder-data databases need >= 35 days backup retention",
        [{"type": "value", "key": "BackupRetentionPeriod", "op": "lt", "value": 35}],
    ),
    policy(
        "pci",
        "s3-access-logging",
        "aws.s3",
        "MEDIUM",
        ["PCI-DSS"],
        "S3 buckets must have access logging enabled",
        [{"type": "bucket-logging", "op": "disabled"}],
    ),
    policy(
        "pci",
        "s3-versioning",
        "aws.s3",
        "LOW",
        ["PCI-DSS"],
        "S3 buckets must have versioning enabled",
        # deliberate: looser than its description (a Suspended status passes), as real policies
        # can be; eval 19 asks for Enabled, so no eval relies on this guardrail for it
        [{"Versioning.Status": "absent"}],
    ),
]
TAGS = [
    policy(
        "sandbox",
        "ec2-owner-tag",
        "aws.ec2",
        "LOW",
        [],
        "EC2 instances need an Owner tag",
        [{"tag:Owner": "absent"}],
    ),
    # A mark-for-op set, as in Custodian's tag-compliance docs: one constraint spread over three
    # policies whose filters differ, so the bundle can't group them; distillation should make them
    # one bullet (instances without an Owner tag are stopped 4 days on unless tagged in time).
    policy(
        "sandbox",
        "ec2-owner-tag-mark",
        "aws.ec2",
        "LOW",
        [],
        "Mark EC2 instances without an Owner tag to be stopped in 4 days",
        [{"tag:Owner": "absent"}, {"tag:c7n_owner_missing": "absent"}],
        actions=[
            {"type": "mark-for-op", "tag": "c7n_owner_missing", "op": "stop", "days": 4}
        ],
    ),
    policy(
        "sandbox",
        "ec2-owner-tag-unmark",
        "aws.ec2",
        "LOW",
        [],
        "Unmark EC2 instances that have been given an Owner tag",
        [{"tag:Owner": "present"}, {"tag:c7n_owner_missing": "present"}],
        actions=[{"type": "remove-tag", "tags": ["c7n_owner_missing"]}],
    ),
    policy(
        "sandbox",
        "ec2-owner-tag-stop",
        "aws.ec2",
        "LOW",
        [],
        "Stop EC2 instances marked for missing an Owner tag",
        [{"type": "marked-for-op", "tag": "c7n_owner_missing", "op": "stop"}],
        actions=[{"type": "stop"}],
    ),
]

# Org-specific rules: deliberately things a model would not guess (exact tag keys/values, CMK-only
# encryption, DLQs, tracing). These are what make the evals discriminate skill from no-skill.
# Their filters check what the evals' checks (tests/evals/skill/*/checks.py) grade: the distillation
# reads a policy's filters, never its description, so a filter looser than its description would
# have the skill tell agents that what the evals fail passes. The deliberate exceptions are marked.
TAG_VALUES = ["public", "internal", "confidential", "restricted"]
# both tags spelled out, rather than trusting how `not-in` treats a missing tag; an `op` needs the
# long form (the `{key: value}` shorthand takes one key, so `{"tag:x": "not-in", "value": ..}` would
# compare against the string "not-in")
TAGS_MISSING = [
    {
        "or": [
            {"tag:cost-center": "absent"},
            {"tag:data-classification": "absent"},
            {
                "type": "value",
                "key": "tag:data-classification",
                "op": "not-in",
                "value": TAG_VALUES,
            },
        ]
    }
]
ORG = [
    policy(
        "acme",
        "required-tags",
        "aws.s3",
        "MEDIUM",
        ["ACME-FINOPS"],
        "Resources must carry tags 'cost-center' and 'data-classification' (value one of public, internal, confidential, restricted)",
        TAGS_MISSING,
    ),
    policy(
        "acme",
        "required-tags-rds",
        "aws.rds",
        "MEDIUM",
        ["ACME-FINOPS"],
        "Resources must carry tags 'cost-center' and 'data-classification' (value one of public, internal, confidential, restricted)",
        TAGS_MISSING,
    ),
    policy(
        "acme",
        "required-tags-ec2",
        "aws.ec2",
        "MEDIUM",
        ["ACME-FINOPS"],
        "Resources must carry tags 'cost-center' and 'data-classification' (value one of public, internal, confidential, restricted)",
        TAGS_MISSING,
    ),
    policy(
        "acme",
        "required-tags-sqs",
        "aws.sqs",
        "MEDIUM",
        ["ACME-FINOPS"],
        "Resources must carry tags 'cost-center' and 'data-classification' (value one of public, internal, confidential, restricted)",
        TAGS_MISSING,
    ),
    policy(
        "acme",
        "required-tags-lambda",
        "aws.lambda",
        "MEDIUM",
        ["ACME-FINOPS"],
        "Resources must carry tags 'cost-center' and 'data-classification' (value one of public, internal, confidential, restricted)",
        TAGS_MISSING,
    ),
    policy(
        "acme",
        "s3-cmk-encryption",
        "aws.s3",
        "HIGH",
        ["ACME-SEC"],
        "S3 buckets must be encrypted with a customer-managed KMS key (SSE-KMS); SSE-S3/AES256 is not acceptable",
        [
            {
                "or": [
                    {"type": "bucket-encryption", "state": False},
                    {"type": "bucket-encryption", "crypto": "AES256"},
                    {
                        "type": "bucket-encryption",
                        "crypto": "aws:kms",
                        "key": "alias/aws/s3",
                    },
                ]
            }
        ],
    ),
    policy(
        "acme",
        "ebs-gp3-only",
        "aws.ebs",
        "LOW",
        ["ACME-FINOPS"],
        "EBS volumes must be gp3; gp2 volumes are not allowed",
        [{"type": "value", "key": "VolumeType", "op": "ne", "value": "gp3"}],
    ),
    policy(
        "acme",
        "ec2-imdsv2",
        "aws.ec2",
        "HIGH",
        ["ACME-SEC"],
        "EC2 instances must require IMDSv2 (http_tokens = required)",
        # deliberate: the description is only the filter's other side (HttpTokens has two values)
        [{"MetadataOptions.HttpTokens": "optional"}],
    ),
    policy(
        "acme",
        "sqs-dead-letter-queue",
        "aws.sqs",
        "MEDIUM",
        ["ACME-RELIABILITY"],
        "SQS queues must have a dead-letter queue configured via a redrive policy with maxReceiveCount <= 5",
        # idealised: SQS returns RedrivePolicy as a JSON string, so a real policy couldn't reach
        # maxReceiveCount with a dotted key. Kept because the evals grade it and only models read
        # these filters; a real deployment's would need a filter that parses it.
        [
            {
                "or": [
                    {"type": "value", "key": "RedrivePolicy", "value": "absent"},
                    {
                        "type": "value",
                        "key": "RedrivePolicy.maxReceiveCount",
                        "op": "gt",
                        "value": 5,
                    },
                ]
            }
        ],
    ),
    policy(
        "acme",
        "sqs-encrypted",
        "aws.sqs",
        "HIGH",
        ["ACME-SEC"],
        "SQS queues must be encrypted with a customer-managed KMS key (kms_master_key_id), not SQS-managed SSE",
        [
            {
                "or": [
                    {"KmsMasterKeyId": "absent"},
                    {"type": "kms-key", "key": "KeyManager", "value": "AWS"},
                ]
            }
        ],
    ),
    policy(
        "acme",
        "lambda-xray-tracing",
        "aws.lambda",
        "LOW",
        ["ACME-RELIABILITY"],
        "Lambda functions must have X-Ray active tracing enabled (tracing_config mode Active)",
        # deliberate: the description is only the filter's other side (Mode is Active or PassThrough)
        [{"TracingConfig.Mode": "PassThrough"}],
    ),
    policy(
        "acme",
        "lambda-reserved-concurrency",
        "aws.lambda",
        "MEDIUM",
        ["ACME-RELIABILITY"],
        "Lambda functions must set reserved concurrency (reserved_concurrent_executions) so one function cannot starve the account",
        [{"type": "reserved-concurrency", "value": "absent"}],
    ),
    policy(
        "acme",
        "ec2-idle-stop",
        "aws.ec2",
        "LOW",
        ["ACME-FINOPS"],
        "Idle EC2 instances (average CPU under 5% for 14 days) are stopped to save cost",
        # runtime-only: nothing an agent writes decides it, but it acts on what the agent defines
        [
            {
                "type": "metrics",
                "name": "CPUUtilization",
                "days": 14,
                "value": 5,
                "op": "less-than",
            }
        ],
        actions=[{"type": "stop"}],
    ),
]

# (collection name, policies, index into GROUPS it is bound to, description)
COLLECTIONS = [
    ("CIS AWS Baseline", CIS, 0, "CIS AWS Foundations benchmark"),
    ("PCI-DSS AWS", PCI, 1, "Controls for systems that store or process card data"),
    ("Sandbox Tagging", TAGS, 2, None),
    ("Acme Org Standards", ORG, 0, ""),
]
GROUPS = [  # name, dynamicFilter, member_count, description
    (
        "All aws accounts",
        "",
        40,
        None,
    ),  # the provider's denominator (see CLAUDE.md quirks)
    ("aws-payments", "tag:team = payments", 3, "Accounts running the payments service"),
    ("aws-sandbox-jdoe", "name = jdoe-sandbox", 1, ""),
]


# the deployment the fixture is from: its cache is CACHE/console.fixture.example/
CONSOLE = "https://console.fixture.example/"

# an account the payments group holds; tests/fixture/ledger-repo deploys to it (eval 11)
LEDGER_ACCOUNT = "111111111111"
# the one account the sandbox group holds: only its lookup brings Sandbox Tagging in;
# tests/fixture/sandbox-repo deploys to it (eval 31)
SANDBOX_ACCOUNT = "222222222222"


def graphql_policy(p):
    """A fixture policy as collection.graphql returns it."""
    return {
        "uuid": p["policy_uuid"],
        "version": p["version"],
        "name": p["name"],
        "provider": p["provider"],
        "resourceType": p["resource_type"],
        "mode": p["mode"],
        "severity": p["severity"],
        "compliance": p["compliance"] or None,
        "source": p["source_json"],
    }


def alias_of(collection):
    """The alias the skill's content queries give a collection (_cache.alias_of)."""
    return "c_" + re.sub(r"\W", "_", collection)


def stamp(path, posture_db, dest=None):
    """Write the collections response at PATH, marked as fetched under the posture in POSTURE_DB
    (its `posture_<refresh id>` alias), to DEST (default: PATH itself), unless it is marked already.
    -> where it is."""
    r = json.loads(Path(path).read_text(encoding="utf-8"))
    data = r.get("data", r) if isinstance(r, dict) else None
    dest = Path(dest or path)
    # not a response (a test's junk) is left for ingest to refuse
    if isinstance(data, dict) and not any(k.startswith("posture_") for k in data):
        db = sqlite3.connect(posture_db)
        (refresh,) = db.execute(
            "SELECT value FROM meta WHERE key = 'refresh_id'"
        ).fetchone()
        db.close()
        data[f"posture_{refresh}"] = "Query"
    dest.write_text(json.dumps(r, indent=1), encoding="utf-8")
    return dest


def write_collections(out, colls, policies, console=CONSOLE):
    """OUT/collections.json: CONSOLE's collection.graphql response for COLLS (posture.graphql's
    shape), POLICIES ({collection uuid: fixture policies}) giving each one's membership and content.
    Shared with make_estate.py."""
    data = {"platform": {"urls": {"console": console}}}
    for c in colls:
        ps = policies.get(c["uuid"], [])
        data[alias_of(c["uuid"])] = {
            "uuid": c["uuid"],
            "policyMappings": {
                "pageInfo": {"total": len(ps), "hasNextPage": False},
                "edges": [{"node": {"policy": graphql_policy(p)}} for p in ps],
            },
        }
    (out / "collections.json").write_text(
        json.dumps({"data": data}, indent=1), encoding="utf-8"
    )


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    groups = [
        {
            "uuid": gid(n),
            "name": n,
            "provider": "AWS",
            "description": desc,
            "dynamicFilter": f,
            "regions": [],
            "accountMappings": {"pageInfo": {"total": c}},
        }
        for n, f, c, desc in GROUPS
    ]
    colls = [
        {
            "uuid": cid(n),
            "name": n,
            "provider": "AWS",
            "description": desc,
            # only its size: the membership is in collections.json
            "policyMappings": {"pageInfo": {"total": len(ps)}},
        }
        for n, ps, _, desc in COLLECTIONS
    ]
    bindings = [
        {
            "uuid": u(f"b{i}"),
            "name": f"{c['name']} -> {g['name']}",
            "description": "PCI scope agreed with the payments team"
            if g["name"] == "aws-payments"
            else None,
            "lastDeployed": "2026-10-01T00:00:00Z",
            "accountGroup": {"uuid": g["uuid"]},
            "policyCollection": {"uuid": c["uuid"]},
        }
        for i, (c, g) in enumerate(
            (c, groups[group]) for c, (_, _, group, _) in zip(colls, COLLECTIONS)
        )
    ]
    # every collection's membership and content in one collection.graphql response, as scope.py
    # query asks for them; the posture keeps only each one's size
    write_collections(out, colls, {cid(n): ps for n, ps, _, _ in COLLECTIONS})
    (out / "posture.json").write_text(
        json.dumps(
            {
                "data": {
                    "platform": {"urls": {"console": CONSOLE}},
                    "bindingPosture": {
                        "accountGroups": groups,
                        "policyCollections": colls,
                        "bindings": bindings,
                    },
                }
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    for i, (key, name, ou, tags, names) in enumerate(
        (
            (
                LEDGER_ACCOUNT,
                "ledger-prod",
                "/Payments/",
                [{"key": "team", "value": "payments"}],
                ("All aws accounts", "aws-payments"),
            ),
            (
                SANDBOX_ACCOUNT,
                "jdoe-sandbox",
                "/Sandbox/",
                [],
                ("All aws accounts", "aws-sandbox-jdoe"),
            ),
        )
    ):
        account = {
            "key": key,
            "name": name,
            "provider": "AWS",
            "path": ou,
            "orgId": "o-acme",
            "tags": tags,
        }
        write_account(
            out / f"account_{i}.json",
            account,
            [g for g in groups if g["name"] in names],
        )


def write_account(path, account, groups, console=CONSOLE):
    """PATH: CONSOLE's account.graphql response for ACCOUNT (key, name, provider, path, orgId,
    tags), active, in GROUPS ({uuid, name} each). Shared with make_estate.py."""
    path.write_text(
        json.dumps(
            {
                "data": {
                    "platform": {"urls": {"console": console}},
                    "account": {
                        **{
                            k: account[k]
                            for k in ("key", "name", "provider", "path", "orgId")
                        },
                        "active": True,
                        "tags": account["tags"],
                        "groupMappings": {
                            "pageInfo": {"total": len(groups), "hasNextPage": False},
                            "edges": [
                                {
                                    "node": {
                                        "group": {"uuid": g["uuid"], "name": g["name"]}
                                    }
                                }
                                for g in groups
                            ],
                        },
                    },
                }
            },
            indent=1,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    if sys.argv[1:2] == ["--stamp"] and len(sys.argv) > 3:
        for f in sys.argv[3:]:
            stamp(f, sys.argv[2])
    elif len(sys.argv) == 2 and not sys.argv[1].startswith("-"):
        main(sys.argv[1])
    else:
        sys.exit(__doc__)
