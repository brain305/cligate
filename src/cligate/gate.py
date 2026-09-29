DANGEROUS = {"rm"}


def is_dangerous(line):
    parts = line.split()
    return bool(parts) and parts[0] in DANGEROUS
