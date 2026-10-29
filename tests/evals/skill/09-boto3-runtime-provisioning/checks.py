import re

from grading import has, nocomments


SHOWS = ("aws.s3", "aws.rds")


def checks(d, t, f):
    py = nocomments(t)
    valid = r"(public|internal|confidential|restricted)"
    tags_ok = (
        has(r"cost-center", py)
        and has(r"data-classification['\"][^\n]{0,60}" + valid, py)
        or (
            has(r"cost-center", py)
            and has(r"data-classification", py)
            and has(r"['\"]" + valid + r"['\"]", py)
        )
    )
    return [
        (
            "Bucket encrypted with an explicit customer-managed KMS key (KMSMasterKeyID), not AES256 or the default key",
            has(r"aws:kms", py)
            and has(r"KMSMasterKeyID", py)
            and not has(r"['\"]AES256['\"]", py),
        ),
        (
            "Bucket public access block with all four flags True",
            has(r"put_public_access_block", py)
            and len(
                re.findall(
                    r"(BlockPublicAcls|IgnorePublicAcls|BlockPublicPolicy|RestrictPublicBuckets)['\"]?\s*[:=]\s*True",
                    py,
                )
            )
            >= 4,
        ),
        (
            "Required tags (cost-center, data-classification with a valid value) applied",
            tags_ok,
        ),
        (
            "RDS StorageEncrypted=True and not PubliclyAccessible",
            # keyword arguments or a params dict: StorageEncrypted=True, "StorageEncrypted": True
            has(r"StorageEncrypted['\"]?\s*[=:]\s*True", py)
            and not has(r"PubliclyAccessible['\"]?\s*[=:]\s*True", py),
        ),
        (
            "RDS create call itself carries Tags and StorageEncrypted (S3's create_bucket can't, so bucket controls just need to follow it)",
            has(r"Tags\s*=", py) and has(r"StorageEncrypted", py),
        ),
    ]
