from grading import TAGS_OK, fetches, has, nocomments


SHOWS = ("aws.ec2", "aws.ebs")


def checks(d, t, f):
    tc = nocomments(t)
    return [
        (
            "Does not ask about payments, card data or PCI (no question bears on EC2 or EBS)",
            not has(r"[^.\n]*(pci|card|payment)[^.\n]*\?", f),
        ),
        ("Data volume is gp3, not gp2", has(r"gp3", tc) and not has(r"\bgp2\b", tc)),
        ("IMDSv2 required", has(r"http_tokens\s*=\s*\"required\"", tc)),
        ("Tags cost-center and data-classification with a valid value", TAGS_OK(t)),
    ]


def server_checks(log, state, calls):
    return [
        (
            "Fetched nothing: the cache is current",
            not fetches(log, "posture") and not fetches(log, "collections"),
        ),
    ]
