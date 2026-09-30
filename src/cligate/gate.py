from . import parse

DANGEROUS = {"rm"}


def is_dangerous(line):
    parts = line.split()
    return bool(parts) and parts[0] in DANGEROUS


def check(commands):
    warnings = []
    for argv in commands:
        real, _ = parse.unwrap(argv)
