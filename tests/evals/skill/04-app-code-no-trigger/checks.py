from grading import SCRIPT, commands, has, read_all


def checks(d, t, f):
    return [
        ("Did not create a .stacklet scope file", not (d / ".stacklet").exists()),
        (
            "Final response does not ask scope questions or list policy collections",
            not has(r"scope\.json|\bPCI\b|\bCIS\b|\bcandidates?\b", f),
        ),
        (
            "Wrote a Python upload function with retry handling",
            has(r"upload_file|put_object|upload_fileobj", read_all(d, {".py"}))
            and has(r"retr", read_all(d, {".py"})),
        ),
    ]


def process_checks(calls, arm):
    return [
        (
            "Ran no guardrail script (the skill doesn't apply)",
            not commands(calls, SCRIPT),
        )
    ]
