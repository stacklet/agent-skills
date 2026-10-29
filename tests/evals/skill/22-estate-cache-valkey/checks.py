from grading import (
    OPT_OUT_ASKED,
    OUT_OF_SCOPE_WAVES,
    blocks,
    has,
    looked_up,
    mrd_tags_ok,
    tags_of,
)


# the trial remediation and the atelier.example collections don't apply here
OUT_OF_SCOPE = OUT_OF_SCOPE_WAVES


def caches(t):
    """The cache resources the change adds (the repo starts empty): the replication group the
    prompt asks for, or clusters standing in for it."""
    return {
        **blocks("aws_elasticache_replication_group", t),
        **blocks("aws_elasticache_cluster", t),
    }


def checks(d, t, f):
    found = caches(t)
    return [
        ('engine = "valkey"', has(r'\bengine\s*=\s*"valkey"', t)),
        (
            "Encrypted at rest and in transit",
            has(r"at_rest_encryption_enabled\s*=\s*true", t)
            and has(r"transit_encryption_enabled\s*=\s*true", t),
        ),
        (
            # the cache's own tags: another resource's (its subnet group's, say) don't count
            "Tags the cache mrd:owner, mrd:service, mrd:environment (dev/stage/prod)",
            bool(found) and all(mrd_tags_ok(tags_of(b, t), t) for b in found.values()),
        ),
        (
            "Does not ask about the GuardrailPause opt-out or billing-sandbox",
            not has(rf"[^.\n]*(?:{OPT_OUT_ASKED}|billing-sandbox)[^.\n]*\?", f),
        ),
    ]


def process_checks(calls, arm):
    if arm == "without_skill":
        return []
    return [
        (
            "Looked up an ElastiCache resource type with distill.py show",
            bool(
                looked_up(calls, "aws.elasticache-group")
                or looked_up(calls, "aws.cache-cluster")
            ),
        )
    ]
