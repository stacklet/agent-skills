from grading import OUT_OF_SCOPE_WAVES, blocks, has, mrd_tags_ok, tags_of


SHOWS = ("aws.ec2", "aws.ebs")
OUT_OF_SCOPE = OUT_OF_SCOPE_WAVES
GRAVITON = r"(c7g|m7g|r7g|t4g|c8g|m8g|r8g)\.\w+"


def compute(t):
    """The instance the prompt asks for, or a launch template standing in for it: what the tagging
    guardrail (aws.ec2) reads; the data volume's tags are another resource's."""
    return {**blocks("aws_instance", t), **blocks("aws_launch_template", t)}


def checks(d, t, f):
    found = compute(t)
    return [
        (
            "A Graviton instance type (literal, or a variable default)",
            has(rf'instance_type\s*=\s*"{GRAVITON}"', t)
            or (
                has(r"instance_type\s*=\s*var\.", t)
                and has(rf'default\s*=\s*"{GRAVITON}"', t)
            ),
        ),
        ("An arm64 AMI", has(r"arm64", t)),
        (
            "gp3 volumes, and no gp2",
            has(r'(type|volume_type)\s*=\s*"gp3"', t) and not has(r'"gp2"', t),
        ),
        (
            "The data volume is encrypted (not only the root volume)",
            any(
                has(r"encrypted\s*=\s*true", b)
                for b in blocks("aws_ebs_volume", t).values()
            )
            or has(r"ebs_block_device\s*\{[^}]*encrypted\s*=\s*true", t),
        ),
        (
            'IMDSv2 required (http_tokens = "required")',
            has(r'http_tokens\s*=\s*"required"', t),
        ),
        (
            "Tags the instance mrd:owner, mrd:service, mrd:environment (dev/stage/prod)",
            bool(found) and all(mrd_tags_ok(tags_of(b, t), t) for b in found.values()),
        ),
    ]
