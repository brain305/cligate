import os
import shlex
import subprocess

# 명령을 가르는 기호에 쓰이는 문자. shlex 는 이어진 기호를 ");" 처럼 한 토큰으로 준다
PUNCTUATION = set("();|&<>")
# $(...) 와 `...` 자리를 대신하는 단어. $ 로 시작해서 명령 자리에 오면 "모르는 명령" 으로 잡힌다
SUBSTITUTION = "$CLIGATE_SUBST"

# 뒤에 오는 명령을 실행하는 명령과, 그중 값을 하나 더 받는 옵션
WRAPPERS = {
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-U", "-C", "-D", "-r", "-t", "-T"},
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "xargs": {"-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a", "-J", "-R", "-S"},
    "command": set(),
    "time": set(),
    "nohup": set(),
}
# 명령 앞에 오는 셸 문법 단어
SHELL_WORDS = {"{", "}", "!", "if", "then", "elif", "else", "while", "until", "do"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


def parse(line):
    masked, inner_lines = mask_substitutions(line)
    commands = split_commands(tokenize(masked))
    # $(...) 안의 명령도 실행되므로 같이 검사 대상에 넣는다
    for inner in inner_lines:
        commands.extend(parse(inner))
    return commands


def tokenize(line):
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # 주석은 mask_substitutions 가 따옴표를 보고 지운다
    return list(lexer)


def mask_substitutions(line):
    """$(...) 와 `...` 를 자리표시자로 바꾸고 주석을 지운다. (바꾼 줄, 안쪽 명령들) 을 돌려준다."""
    out = []
    inner_lines = []
    quote = None
    i = 0
    while i < len(line):
        c = line[i]
        if quote == "'":
            quote = None if c == "'" else quote
        elif c == "\\":
            out.append(line[i:i + 2])
            i += 2
            continue
        elif line.startswith("$(", i):
            end = _closing_paren(line, i + 2)
            inner_lines.append(line[i + 2:end])
            out.append(SUBSTITUTION)
            i = end + 1
            continue
        elif c == "`":
            end = line.find("`", i + 1)
            end = len(line) if end == -1 else end
            inner_lines.append(line[i + 1:end])
            out.append(SUBSTITUTION)
            i = end + 1
            continue
        elif c == '"':
            quote = None if quote == '"' else '"'
        elif quote is None and c == "'":
            quote = "'"
        elif quote is None and c == "#" and (i == 0 or line[i - 1].isspace()):
            break
        out.append(c)
        i += 1
    return "".join(out), inner_lines


def _closing_paren(line, start):
    depth = 1
    quote = None
    i = start
    while i < len(line):
        c = line[i]
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(line)


def split_commands(tokens):
    commands = []
    current = []
    for token in tokens:
        if _is_separator(token):
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def _is_separator(token):
    chars = set(token)
    if not chars <= PUNCTUATION:
        return False
    if chars & set("()"):
        return True
    return not chars & set("<>")  # > 와 2>&1 은 명령을 가르지 않는다


def syntax_error(line):
    r = subprocess.run(["/bin/sh", "-n", "-c", line],
                       capture_output=True, text=True)
    return r.stderr.strip() if r.returncode != 0 else None


def strip_redirects(words):
    """> out 과 2>&1 을 뺀다. 파일 이름이나 번호를 명령의 인자로 착각하지 않게 한다."""
    kept = []
    skip_next = False
    for word in words:
        if skip_next:
            skip_next = False
        elif word and set(word) <= set("<>&"):
            if kept and kept[-1].isdigit():
                kept.pop()  # 2>&1 의 2
            skip_next = True
        else:
            kept.append(word)
    return kept


def unwrap(argv):
    """앞에 붙은 sudo 등을 벗겨 진짜 명령을 찾는다. (진짜 명령, 벗겨낸 것) 을 돌려준다."""
    i = 0
    wrapper = None
    while i < len(argv):
        word = argv[i]
        is_assign = "=" in word and word.split("=", 1)[0].isidentifier()  # FOO=1 rm x
        if word == "command" and argv[i + 1:i + 2] in (["-v"], ["-V"]):
            break  # command -v rm 은 rm 을 실행하지 않고 찾기만 한다
        if word in SHELL_WORDS or is_assign:
            i += 1
        elif word in WRAPPERS:
            wrapper = word
            i += 1
        elif wrapper and word.startswith("-"):
            i += 2 if word in WRAPPERS[wrapper] else 1
        else:
            break
    return argv[i:], argv[:i]


def inner_line(real):
    """eval "..." 과 sh -c "..." 에 문자열로 들어 있는 명령을 꺼낸다. 없으면 None."""
    name = os.path.basename(real[0])
    if name == "eval":
        return " ".join(real[1:])
    if name in SHELLS:
        for i, word in enumerate(real[1:], start=1):
            if word.startswith("-") and not word.startswith("--") and "c" in word:
                return real[i + 1] if i + 1 < len(real) else None
    return None


def find_exec(real):
    """find ... -exec 명령 {} ; 에서 실행될 명령들을 꺼낸다."""
    commands = []
    words = real[1:]
    for i, word in enumerate(words):
        if word in ("-exec", "-execdir", "-ok", "-okdir"):
            command = []
            for arg in words[i + 1:]:
                if arg in (";", "+"):
                    break
                command.append(arg)
            commands.append(command)
    return commands
