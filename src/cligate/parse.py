import shlex
import subprocess

SEPERATORS = {";", "&&", "||", "|", "&"}


def parse(line):
    tokens = tokenize(line)
    return split_commands(tokens)


def tokenize(line):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def split_commands(tokens):
    commands = []
    current = []
    for token in tokens:
        if token in SEPERATORS:
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def syntax_error(line):
    r = subprocess.run(["/bin/sh", "-n", "-c", line],
                       capture_output=True, text=True)
    return r.stderr.strip() if r.returncode != 0 else None
