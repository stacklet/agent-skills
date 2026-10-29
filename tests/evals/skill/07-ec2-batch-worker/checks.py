from grading import TAGS_OK, has, ingress_open_world, nocomments


SHOWS = ("aws.ec2", "aws.ebs")


def checks(d, t, f):
    tc = nocomments(t)
    return [
        (
            "Data volume is gp3, not gp2",
            has(r"gp3", tc) and not has(r"\bgp2\b", tc),
        ),
        (
            "IMDSv2 required (http_tokens = required)",
            has(r"http_tokens\s*=\s*\"required\"", tc),
        ),
        ("Tags cost-center and data-classification with a valid value", TAGS_OK(t)),
        (
            "SSH limited to 203.0.113.0/24; no ingress from 0.0.0.0/0",
            has(r"203\.0\.113\.0/24", tc) and not ingress_open_world(t),
        ),
        ("Volume encrypted", has(r"encrypted\s*=\s*true", tc)),
    ]
