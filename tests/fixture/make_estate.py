"""Generate a synthetic posture shaped like a real large estate's, for the skill's evals (22-28;
the posture assessment has its own, tests/evals/assess): posture.json, collections.json and account
lookups (account_*.json).

usage: make_estate.py OUTDIR
Then, with STACKLET_GUARDRAILS_CACHE=CACHEDIR: <skill dir>/scripts/ingest.py console.estate.example
posture OUTDIR/posture.json, make_fixture.py --stamp CACHEDIR/console.estate.example/posture.db
OUTDIR/collections.json, ingest.py console.estate.example collections OUTDIR/collections.json, and
ingest.py console.estate.example accounts OUTDIR/account_*.json

Every name, id, tag and count is invented; only the shapes are real, each one something a model has
to read groups together to see:
- AWS guardrails rolled out over "waves": groups by a RolloutWave tag value (dev and prod alpha,
  beta, gamma, plus a restricted wave), each 2-21% of the accounts, together all of them but the
  canary account (a wave of its own) and the ones opted out with a GuardrailPause tag. Each wave
  has three group variants: "-standard"; "-idn-exempt", whose filter is identical though its
  description claims an extra exemption; "-retain-exempt", which also excludes the account named
  billing-sandbox. Every collection rolled out over the waves is bound to wave groups only, so
  the opt-out takes an account out of all of them.
- A one-account "enforcement" pilot group (by name, so no opt-out) holding only a report of its own
  and an empty collection.
- "-prod-" collections bound to dev waves too, so a name says nothing about environment.
- Collections bound to the whole org (every AWS account) or to all accounts.
- A trial remediation group (one hand-picked account, two regions) whose policies delete resources.
- Canary batches (one or two accounts) holding a staged collection.
- An empty collection; boilerplate descriptions; binding descriptions that only name their group.
- GCP projects split between a main org and an acquired one, atelier.example (plus one
  standalone); a baseline collection bound only to the acquired org; wave labels on 66% of projects.
- Many groups nothing is bound to.

Account lookups: AWS 210987654321 (orders-prod, prod beta wave), 345678901234 (analytics-03:
GuardrailPause tag, so in no wave group), 456789012345 (lab-7, the trial remediation account); GCP
atelier-render-prod (atelier.example org) and meridian-web-prod (main org).
"""

import json
import sys
import uuid

from pathlib import Path

from make_fixture import write_account, write_collections


CONSOLE = "https://console.estate.example/"
AWS_ORG = "o-example0001"
GCP_ORGS = {"100000000001": "meridian.example", "100000000002": "atelier.example"}
AWS_TOTAL, GCP_TOTAL = 160, 140
AWS_REGIONS = [
    "ap-northeast-1",
    "ap-south-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ca-central-1",
    "eu-central-1",
    "eu-north-1",
    "eu-west-1",
    "eu-west-2",
    "sa-east-1",
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
]
PAUSE = "GuardrailPause"
EXEMPT = "billing-sandbox"


def u(name):
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, "estate:" + name))


# Filters, written as the platform stores them.


def single(name, value=None, operator=None, values=None):
    s = {"name": name}
    if operator:
        s["operator"] = operator
    if values is not None:
        s["values"] = values
    elif value is not None:
        s["value"] = value
    return {"single": s}


def multiple(operator, *operands):
    return {"multiple": {"operands": list(operands), "operator": operator}}


NOT_PAUSED = multiple("NOT", single(f"tag:{PAUSE}", operator="exists"))
NOT_EXEMPT = multiple("NOT", multiple("OR", single("name", EXEMPT, operator="equals")))


def wave_filter(wave, exempt=False):
    terms = [single("tag:RolloutWave", wave), NOT_PAUSED]
    if exempt:
        terms.append(NOT_EXEMPT)
    return json.dumps(multiple("AND", *terms))


def org_filter(org):
    return json.dumps(single("org", operator="equals", values=[org]))


# Policies: invented, deliberately things a model wouldn't guess.


def policy(
    ns, name, resource, severity, description, filters, actions=None, mode="pull"
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
        "policy_uuid": u("p:" + full),
        "version": 1,
        "name": full,
        "provider": "GCP" if resource.startswith("gcp.") else "AWS",
        "resource_type": resource,
        "mode": mode,
        "severity": severity,
        "compliance": [],
        "source_json": json.dumps(src),
    }


REQUIRED_TAGS = ["mrd:owner", "mrd:service", "mrd:environment"]
ENVIRONMENTS = ["dev", "stage", "prod"]
TAGS_MISSING = [
    {
        "or": [
            *({f"tag:{k}": "absent"} for k in REQUIRED_TAGS),
            {
                "type": "value",
                "key": "tag:mrd:environment",
                "op": "not-in",
                "value": ENVIRONMENTS,
            },
        ]
    }
]
GRAVITON = ["c7g", "m7g", "r7g", "t4g", "c8g", "m8g", "r8g"]
TLS_POLICY = "ELBSecurityPolicy-TLS13-1-2-Res-2021-06"

P = {
    "cis-aws": [
        policy(
            "cis",
            "s3-encrypted",
            "aws.s3",
            "HIGH",
            "S3 buckets must be encrypted",
            [{"type": "bucket-encryption", "state": False}],
        ),
        policy(
            "cis",
            "rds-not-public",
            "aws.rds",
            "CRITICAL",
            "RDS instances must not be public",
            [{"PubliclyAccessible": True}],
        ),
        policy(
            "cis",
            "sg-no-world-ssh",
            "aws.security-group",
            "CRITICAL",
            "No 0.0.0.0/0 on port 22",
            [{"type": "ingress", "Ports": [22], "Cidr": "0.0.0.0/0"}],
        ),
        policy(
            "cis",
            "ebs-encrypted",
            "aws.ebs",
            "HIGH",
            "EBS volumes must be encrypted",
            [{"Encrypted": False}],
        ),
    ],
    "soc2-aws": [
        policy(
            "soc2",
            "rds-backups-7d",
            "aws.rds",
            "MEDIUM",
            "RDS backups kept at least 7 days",
            [
                {
                    "type": "value",
                    "key": "BackupRetentionPeriod",
                    "op": "lt",
                    "value": 7,
                }
            ],
        ),
    ],
    "load-balancer-inventory": [
        policy(
            "mrd",
            "load-balancer-inventory",
            "aws.app-elb",
            "LOW",
            "Inventory of application load balancers",
            [],
        ),
    ],
    "aws-baseline": [
        policy(
            "baseline",
            "ec2-imdsv2",
            "aws.ec2",
            "HIGH",
            "EC2 instances should require IMDSv2",
            [{"MetadataOptions.HttpTokens": "optional"}],
        ),
        policy(
            "baseline",
            "s3-public-block",
            "aws.s3",
            "HIGH",
            "S3 buckets should block public access",
            [{"type": "check-public-block", "BlockPublicAcls": False}],
        ),
    ],
    "aws-tag-review": [
        policy(
            "baseline",
            "s3-owner-tag",
            "aws.s3",
            "LOW",
            "Report buckets without an owner tag",
            [{"tag:mrd:owner": "absent"}],
        ),
    ],
    "encrypt-at-rest-enforce": [
        policy(
            "mrd",
            "s3-cmk",
            "aws.s3",
            "HIGH",
            "S3 buckets must use a customer-managed KMS key",
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
            "mrd",
            "rds-cmk",
            "aws.rds",
            "HIGH",
            "RDS storage must be encrypted with a customer-managed KMS key",
            [
                {
                    "or": [
                        {"StorageEncrypted": False},
                        {"type": "kms-key", "key": "KeyManager", "value": "AWS"},
                    ]
                }
            ],
        ),
        policy(
            "mrd",
            "elasticache-encryption",
            "aws.elasticache-group",
            "HIGH",
            "Cache replication groups must encrypt at rest and in transit",
            [
                {
                    "or": [
                        {"AtRestEncryptionEnabled": False},
                        {"TransitEncryptionEnabled": False},
                    ]
                }
            ],
        ),
    ],
    "private-endpoints-enforce": [
        policy(
            "mrd",
            "rds-private",
            "aws.rds",
            "CRITICAL",
            "RDS instances must not be publicly accessible",
            [{"PubliclyAccessible": True}],
        ),
    ],
    "tls-policy-enforce": [
        policy(
            "mrd",
            "alb-tls-policy",
            "aws.app-elb",
            "HIGH",
            "HTTPS listeners must use the approved TLS policy",
            [
                {
                    "type": "listener",
                    "key": "SslPolicy",
                    "op": "ne",
                    "value": TLS_POLICY,
                    "matched": False,
                }
            ],
        ),
    ],
    "cache-engine-standard": [
        policy(
            "mrd",
            "elasticache-valkey-group",
            "aws.elasticache-group",
            "MEDIUM",
            "Cache replication groups must run Valkey, not Redis OSS",
            [{"type": "value", "key": "Engine", "op": "ne", "value": "valkey"}],
        ),
        policy(
            "mrd",
            "elasticache-valkey-cluster",
            "aws.cache-cluster",
            "MEDIUM",
            "Cache clusters must run Valkey, not Redis OSS",
            [{"type": "value", "key": "Engine", "op": "ne", "value": "valkey"}],
        ),
    ],
    "iam-user-purpose-tag": [
        policy(
            "mrd",
            "iam-user-purpose",
            "aws.iam-user",
            "MEDIUM",
            "IAM users need a purpose tag",
            [{"tag:mrd:purpose": "absent"}],
        ),
    ],
    "arm-compute-standard": [
        policy(
            "mrd",
            "ec2-graviton",
            "aws.ec2",
            "MEDIUM",
            "EC2 instances must use Graviton (arm64) instance families",
            [
                {
                    "type": "value",
                    "key": "InstanceType",
                    "op": "regex",
                    "value": f"^(?!({'|'.join(GRAVITON)})\\.).*",
                }
            ],
        ),
        policy(
            "mrd",
            "rds-graviton",
            "aws.rds",
            "MEDIUM",
            "RDS instances must use Graviton instance classes",
            [
                {
                    "type": "value",
                    "key": "DBInstanceClass",
                    "op": "regex",
                    "value": "^db\\.(?!(m7g|r7g|t4g|m8g|r8g)\\.).*",
                }
            ],
        ),
    ],
    "resource-tagging-enforce": [
        policy(
            "mrd",
            f"required-tags-{r.split('.')[1]}",
            r,
            "MEDIUM",
            "Resources need mrd:owner, mrd:service and mrd:environment (dev, stage or prod)",
            TAGS_MISSING,
        )
        for r in (
            "aws.s3",
            "aws.rds",
            "aws.ec2",
            "aws.elasticache-group",
            "aws.sqs",
            "aws.lambda",
        )
    ],
    "volume-type-standard": [
        policy(
            "mrd",
            "ebs-gp3",
            "aws.ebs",
            "LOW",
            "EBS volumes must be gp3",
            [{"type": "value", "key": "VolumeType", "op": "ne", "value": "gp3"}],
        ),
    ],
    "stream-encryption-remediate": [
        policy(
            "mrd",
            "kinesis-encryption-mark",
            "aws.kinesis",
            "HIGH",
            "Mark unencrypted streams for deletion in 3 days",
            [{"EncryptionType": "NONE"}, {"tag:mrd_unencrypted": "absent"}],
            actions=[
                {
                    "type": "mark-for-op",
                    "tag": "mrd_unencrypted",
                    "op": "delete",
                    "days": 3,
                }
            ],
        ),
        policy(
            "mrd",
            "kinesis-encryption-delete",
            "aws.kinesis",
            "HIGH",
            "Delete streams marked as unencrypted",
            [{"type": "marked-for-op", "tag": "mrd_unencrypted", "op": "delete"}],
            actions=[{"type": "delete"}],
        ),
    ],
    "encrypt-at-rest-advise": [
        policy(
            "mrd",
            "dynamodb-cmk-notify",
            "aws.dynamodb-table",
            "LOW",
            "Notify on tables without a customer-managed key",
            [{"SSEDescription": "absent"}],
            actions=[{"type": "notify", "to": ["cloud-security@meridian.example"]}],
        ),
    ],
    "private-endpoints-advise": [
        policy(
            "mrd",
            "s3-acl-notify",
            "aws.s3",
            "LOW",
            "Notify on buckets with public ACL grants",
            [{"type": "global-grants"}],
            actions=[{"type": "notify", "to": ["cloud-security@meridian.example"]}],
        ),
    ],
    "cert-validation-standard": [
        policy(
            "mrd",
            "acm-dns-validation",
            "aws.acm-certificate",
            "LOW",
            "Certificates must use DNS validation",
            [
                {
                    "type": "value",
                    "key": "DomainValidationOptions[0].ValidationMethod",
                    "value": "EMAIL",
                }
            ],
        ),
    ],
    "retention-tag-misuse-report": [
        policy(
            "mrd",
            "deletion-protection-tag-report",
            "aws.sns",
            "LOW",
            "Report topics tagged for deletion protection, which they cannot have",
            [{"tag:mrd:deletion-protection": "present"}],
        ),
    ],
    "identity-change-notify": [
        policy(
            "mrd",
            "permission-set-created",
            "aws.sso-permission-set",
            "LOW",
            "Notify when permission sets are created",
            [],
            actions=[{"type": "notify", "to": ["identity@meridian.example"]}],
            mode="cloudtrail",
        ),
    ],
    "identity-store-retain": [
        policy(
            "mrd",
            "identity-store-protect",
            "aws.sso-instance",
            "MEDIUM",
            "Protect the identity store from deletion",
            [],
            actions=[{"type": "tag", "tags": {"mrd:deletion-protection": "true"}}],
        ),
    ],
    "live-remediation": [
        policy(
            "trial",
            f"{r.split('.')[1]}-delete-untagged",
            r,
            "HIGH",
            "Delete resources missing an Owner tag",
            [{"tag:Owner": "absent"}],
            actions=[{"type": "delete"}],
            mode="cloudtrail",
        )
        for r in ("aws.s3", "aws.ec2", "aws.sqs")
    ],
    "dev-genai-usage-report": [
        policy(
            "mrd",
            "genai-agent-usage",
            "aws.bedrock-agent",
            "LOW",
            "Report agents invoked directly",
            [],
        ),
    ],
    "dev-snapshot-tagging": [
        policy(
            "mrd",
            "snapshot-tag-inheritance-dev",
            "aws.ebs-snapshot",
            "LOW",
            "Snapshots should copy volume tags",
            [{"tag:mrd:owner": "absent"}],
        ),
    ],
    "cis-gcp": [
        policy(
            "cis",
            "gcs-uniform-access",
            "gcp.bucket",
            "HIGH",
            "Buckets must use uniform bucket-level access",
            [{"iamConfiguration.uniformBucketLevelAccess.enabled": False}],
        ),
        policy(
            "cis",
            "sql-no-public-ip",
            "gcp.sql-instance",
            "CRITICAL",
            "Cloud SQL must not have a public IP",
            [{"settings.ipConfiguration.ipv4Enabled": True}],
        ),
    ],
    "cost-gcp": [
        policy(
            "cost",
            "disk-unattached",
            "gcp.disk",
            "LOW",
            "Unattached disks",
            [{"users": "absent"}],
        ),
    ],
    "gcp-baseline": [
        policy(
            "baseline",
            "gcs-atelier-labels",
            "gcp.bucket",
            "MEDIUM",
            "Buckets need atelier-team and cost-code labels",
            [
                {
                    "or": [
                        {"labels.atelier-team": "absent"},
                        {"labels.cost-code": "absent"},
                    ]
                }
            ],
        ),
        policy(
            "baseline",
            "gcs-versioning",
            "gcp.bucket",
            "MEDIUM",
            "Buckets must have versioning enabled",
            [{"versioning.enabled": "absent"}],
        ),
    ],
    "gcp-tag-review": [
        policy(
            "baseline",
            "gce-atelier-labels",
            "gcp.instance",
            "LOW",
            "Instances need a atelier-team label",
            [{"labels.atelier-team": "absent"}],
        ),
    ],
    "gcp-ml-api-inventory": [
        policy(
            "mrd",
            "vertex-enabled",
            "gcp.service",
            "LOW",
            "Inventory of projects with Vertex AI enabled",
            [],
        ),
    ],
    "gcp-labels-advise": [
        policy(
            "mrd",
            "gcs-required-labels",
            "gcp.bucket",
            "LOW",
            "Buckets need owner and environment labels",
            [{"or": [{"labels.owner": "absent"}, {"labels.environment": "absent"}]}],
        ),
    ],
}


# Account groups: (name, provider, filter, members, description, regions or None for the default).
WAVES = [  # wave tag value, accounts in it (AWS); the paused ones are in none
    ("DevAlphaWave", 22),
    ("DevBetaWave", 21),
    ("DevGammaWave", 33),
    ("ProdAlphaWave", 24),
    ("ProdBetaWave", 23),
    ("ProdGammaWave", 31),
    ("ProdRestrictedWave", 3),
]  # 157 of 160: the canary account is in a wave of its own, two carry GuardrailPause
GCP_WAVES = [  # wave label value, group slug, projects in it
    ("devalphawave", "dev-alpha", 20),
    ("devbetawave", "dev-beta", 19),
    ("devgammawave", "dev-gamma", 22),
    ("prodalphawave", "prod-alpha", 12),
    ("prodbetawave", "prod-beta", 11),
    ("prodgammawave", "prod-gamma", 8),
    ("prodrestrictedwave", "prod-restricted", 1),
]  # 93 of 140 projects carry a wave label


def slug(wave):
    """DevAlphaWave -> dev-alpha"""
    words = "".join(c if c.islower() else " " + c.lower() for c in wave).split()
    return "-".join(words[:-1])


def wave_groups():
    out = []
    for wave, n in WAVES:
        s = slug(wave)
        title = s.replace("-", " ").capitalize()
        out += [
            (
                f"mrd-aws-{s}-wave-standard",
                "AWS",
                wave_filter(wave),
                n,
                None if s.startswith("dev") else f"{title} wave, standard guardrails",
                None,
            ),
            (
                f"mrd-aws-{s}-wave-idn-exempt",
                "AWS",
                wave_filter(wave),
                n,
                f"{title} wave, without paused accounts or the identity-notification exemptions",
                None,
            ),
            (
                f"mrd-aws-{s}-wave-retain-exempt",
                "AWS",
                wave_filter(wave, exempt=True),
                n - (1 if wave == "DevAlphaWave" else 0),
                f"{title} wave, without paused accounts or the retention-exempt accounts",
                None,
            ),
        ]
    for wave, s, n in GCP_WAVES:
        name = f"mrd-gcp-{s}-wave"
        out.append(
            (name, "GCP", f"tag:rolloutwave={wave}", n, name, None)
        )  # legacy filters, descriptions that only repeat the name
    return out


GROUPS = [
    (
        "All aws accounts",
        "AWS",
        "",
        AWS_TOTAL,
        "Created automatically to reflect all aws accounts",
        None,
    ),
    (
        f"All {AWS_ORG} accounts",
        "AWS",
        org_filter(AWS_ORG),
        AWS_TOTAL,
        f"Created automatically to reflect all accounts within the {AWS_ORG} organisation.",
        None,
    ),
    (
        "All gcp accounts",
        "GCP",
        "",
        GCP_TOTAL,
        "Created automatically to reflect all gcp accounts",
        None,
    ),
    (
        "All meridian.example accounts",
        "GCP",
        org_filter("100000000001"),
        108,
        "Created automatically to reflect all accounts within the meridian.example organisation.",
        None,
    ),
    (
        "All atelier.example accounts",
        "GCP",
        org_filter("100000000002"),
        31,
        "Created automatically to reflect all accounts within the atelier.example organisation.",
        None,
    ),
    *wave_groups(),
    ("Trial Remediation", "AWS", None, 1, None, ["us-east-1", "us-east-2"]),
    (
        "mrd-aws-enforcement-pilot-group",
        "AWS",
        "secops-enforced-dev",
        1,
        "Pilot accounts for enforcement",
        None,
    ),
    (
        "mrd-aws-canary-wave",
        "AWS",
        "tag:RolloutWave=CanaryWave",
        1,
        "Canary wave",
        None,
    ),
    ("mrd-aws-identity-notify-accounts", "AWS", "idm-core,idm-stage", 2, None, None),
    (
        "mrd-gcp-canary-wave",
        "GCP",
        "tag:rolloutwave=canarywave",
        2,
        "mrd-gcp-canary-wave",
        None,
    ),
    # nothing is bound to these: per-account and per-team groups people made and left
    *(
        (f"acct-{i:03d}-group", "AWS", f"acct-{i:03d}", 1, None, None)
        for i in range(60)
    ),
    *((f"team-{t}-projects", "GCP", f"tag:team={t}", 4, None, None) for t in range(25)),
]


def wave_set(variant, extra=()):
    return [f"mrd-aws-{slug(w)}-wave-{variant}" for w, _ in WAVES] + list(extra)


GCP_WAVE_SET = [g[0] for g in wave_groups() if g[1] == "GCP"] + ["mrd-gcp-canary-wave"]

# (collection name, provider, policies key or None for empty, groups it is bound to, description,
# binding description or None for the generated one)
DASHBOARD = "All aws policies needed for {} dashboards"
COLLECTIONS = [
    (
        "aws policies for cis-aws",
        "AWS",
        "cis-aws",
        ["All aws accounts"],
        DASHBOARD.format("cis-aws"),
        "Runs all aws policies needed for cis-aws dashboards",
    ),
    (
        "aws policies for soc2-aws",
        "AWS",
        "soc2-aws",
        ["All aws accounts"],
        DASHBOARD.format("soc2-aws"),
        "Runs all aws policies needed for soc2-aws dashboards",
    ),
    (
        "mrd-prod-aws-load-balancer-inventory",
        "AWS",
        "load-balancer-inventory",
        ["All aws accounts"],
        "Stacklet policy collection: load-balancer-inventory",
        None,
    ),
    (
        "AWS - Baseline Review",
        "AWS",
        "aws-baseline",
        [f"All {AWS_ORG} accounts"],
        None,
        None,
    ),
    (
        "AWS - Tag Review",
        "AWS",
        "aws-tag-review",
        [f"All {AWS_ORG} accounts"],
        None,
        None,
    ),
    *(
        (
            f"mrd-prod-aws-{k}",
            "AWS",
            k,
            wave_set("standard"),
            f"Stacklet policy collection: {k}",
            None,
        )
        for k in (
            "encrypt-at-rest-enforce",
            "private-endpoints-enforce",
            "tls-policy-enforce",
            "cache-engine-standard",
            "iam-user-purpose-tag",
            "arm-compute-standard",
            "resource-tagging-enforce",
            "volume-type-standard",
            "stream-encryption-remediate",
            "encrypt-at-rest-advise",
            "private-endpoints-advise",
            "cert-validation-standard",
        )
    ),
    (
        "mrd-prod-aws-retention-tag-misuse-report",
        "AWS",
        "retention-tag-misuse-report",
        wave_set("standard"),
        "Stacklet policy collection: retention-tag-misuse-report",
        None,
    ),
    (
        "mrd-prod-aws-identity-change-notify",
        "AWS",
        "identity-change-notify",
        wave_set(
            "idn-exempt",
            ["mrd-aws-canary-wave", "mrd-aws-identity-notify-accounts"],
        ),
        "Stacklet policy collection: identity-change-notify",
        None,
    ),
    (
        "mrd-prod-aws-identity-store-retain",
        "AWS",
        "identity-store-retain",
        wave_set("retain-exempt"),
        "Stacklet policy collection: identity-store-retain",
        None,
    ),
    (
        "AWS - Live Remediation",
        "AWS",
        "live-remediation",
        ["Trial Remediation"],
        "Remediation trial: removes resources that miss the owner tagging rules, as soon as they appear",
        None,
    ),
    (
        "mrd-dev-aws-genai-usage-report",
        "AWS",
        "dev-genai-usage-report",
        ["mrd-aws-enforcement-pilot-group"],
        "Stacklet policy collection: dev-genai-usage-report",
        None,
    ),
    (
        "mrd-prod-aws-identity-create-notify",
        "AWS",
        None,
        ["mrd-aws-enforcement-pilot-group"],
        None,
        None,
    ),
    (
        "mrd-dev-aws-snapshot-tagging",
        "AWS",
        "dev-snapshot-tagging",
        ["mrd-aws-canary-wave"],
        "Stacklet policy collection: dev-snapshot-tagging",
        None,
    ),
    (
        "gcp policies for cis-gcp",
        "GCP",
        "cis-gcp",
        ["All gcp accounts"],
        "All gcp policies needed for cis-gcp dashboards",
        None,
    ),
    (
        "gcp policies for cost-gcp",
        "GCP",
        "cost-gcp",
        ["All gcp accounts"],
        "All gcp policies needed for cost-gcp dashboards",
        None,
    ),
    (
        "GCP - Baseline Review",
        "GCP",
        "gcp-baseline",
        ["All atelier.example accounts"],
        None,
        None,
    ),
    (
        "GCP - Tag Review",
        "GCP",
        "gcp-tag-review",
        ["All atelier.example accounts"],
        None,
        None,
    ),
    (
        "mrd-prod-gcp-ml-api-inventory",
        "GCP",
        "gcp-ml-api-inventory",
        GCP_WAVE_SET,
        "Stacklet policy collection: gcp-ml-api-inventory",
        None,
    ),
    (
        "mrd-prod-gcp-labels-advise",
        "GCP",
        "gcp-labels-advise",
        ["mrd-gcp-canary-wave"],
        "Stacklet policy collection: gcp-labels-advise",
        None,
    ),
    # bound to nothing deployed: a collection someone made and never bound
    ("mrd-aws-draft-collection", "AWS", "dev-snapshot-tagging", [], None, None),
]


# Account lookups: (provider, key, name, org, tags, the groups it is in)
def in_wave(wave):
    s = slug(wave)
    return [
        f"mrd-aws-{s}-wave-{v}" for v in ("standard", "idn-exempt", "retain-exempt")
    ]


AWS_ALL = ["All aws accounts", f"All {AWS_ORG} accounts"]
ACCOUNTS = [
    (
        "AWS",
        "210987654321",
        "orders-prod",
        AWS_ORG,
        {"RolloutWave": "ProdBetaWave", "team": "orders"},
        AWS_ALL + in_wave("ProdBetaWave"),
    ),
    (
        "AWS",
        "345678901234",
        "analytics-03",
        AWS_ORG,
        {"RolloutWave": "ProdAlphaWave", PAUSE: "until-2027-01"},
        AWS_ALL,
    ),
    (
        "AWS",
        "456789012345",
        "lab-7",
        AWS_ORG,
        {"RolloutWave": "DevAlphaWave"},
        AWS_ALL + in_wave("DevAlphaWave") + ["Trial Remediation"],
    ),
    (
        "GCP",
        "atelier-render-prod",
        "atelier-render-prod",
        "100000000002",
        {"label": "rolloutwave:prodgammawave"},
        ["All gcp accounts", "All atelier.example accounts", "mrd-gcp-prod-gamma-wave"],
    ),
    (
        "GCP",
        "meridian-web-prod",
        "meridian-web-prod",
        "100000000001",
        {},
        ["All gcp accounts", "All meridian.example accounts"],
    ),
]


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    groups = {
        n: {
            "uuid": u("g:" + n),
            "name": n,
            "provider": p,
            "description": d,
            "dynamicFilter": f,
            "regions": r if r is not None else (AWS_REGIONS if p == "AWS" else []),
            "accountMappings": {"pageInfo": {"total": m}},
        }
        for n, p, f, m, d, r in GROUPS
    }
    colls, bindings = [], []
    for n, p, key, gs, d, bd in COLLECTIONS:
        cu = u("c:" + n)
        ps = P[key] if key else []
        colls.append(
            {
                "uuid": cu,
                "name": n,
                "provider": p,
                "description": d,
                # only its size: the membership is in collections.json
                "policyMappings": {"pageInfo": {"total": len(ps)}},
            }
        )
        for g in gs:
            bindings.append(
                {
                    "uuid": u(f"b:{n}:{g}"),
                    "name": f"{g}-{n}",
                    "description": bd or f"Compliance policies for {g} accounts",
                    "lastDeployed": "2026-10-01T00:00:00+00:00",
                    "accountGroup": {"uuid": groups[g]["uuid"]},
                    "policyCollection": {"uuid": cu},
                }
            )
    orgs = [{"key": AWS_ORG, "name": AWS_ORG, "provider": "AWS"}] + [
        {"key": k, "name": n, "provider": "GCP"} for k, n in GCP_ORGS.items()
    ]
    # every collection's membership and content in one collection.graphql response, as scope.py
    # query asks for them; the posture keeps only each one's size
    write_collections(
        out,
        colls,
        {u("c:" + n): P[key] if key else [] for n, _, key, _, _, _ in COLLECTIONS},
        console=CONSOLE,
    )
    (out / "posture.json").write_text(
        json.dumps(
            {
                "data": {
                    "platform": {"urls": {"console": CONSOLE}},
                    "cloudOrganizations": {
                        "pageInfo": {"hasNextPage": False},
                        "edges": [{"node": o} for o in orgs],
                    },
                    "bindingPosture": {
                        "accountGroups": list(groups.values()),
                        "policyCollections": colls,
                        "bindings": bindings,
                    },
                }
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    for i, (p, key, name, org, tags, gs) in enumerate(ACCOUNTS):
        tags = [{"key": k, "value": v} for k, v in tags.items()] or None
        account = {
            "key": key,
            "name": name,
            "provider": p,
            "path": None,
            "orgId": org,
            "tags": tags,
        }
        write_account(
            out / f"account_{i}.json", account, [groups[g] for g in gs], console=CONSOLE
        )


if __name__ == "__main__":
    main(sys.argv[1])
