import glob
import os
import re

from . import APP_NAME, gitpush, gitwork, parse, risk, state
from .risk import Finding, L1, L2, L3, R2, R3, Verdict

DANGEROUS = {"rm"}
SYSTEM_DIRS = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/opt", "/sbin", "/usr", "/var",
               "/Applications", "/Library", "/System", "/Users"}
CRITICAL_DIR = "루트·홈·시스템 폴더"


def is_dangerous(line):
    parts = line.split()
    return bool(parts) and parts[0] in DANGEROUS


def check(commands):
    """한 줄을 판정해 Verdict 를 돌려준다. 등급은 명령과 지금 상태의 조합으로 정한다."""
    findings = []
    cwd = os.getcwd()
    for real, wrappers in _walk(commands):
        name = os.path.basename(real[0])
        if real[0].startswith("$"):
            findings.append(Finding(L1, f"{APP_NAME}: 실행 전에는 무슨 명령인지 알 수 없어 확인하지 못했습니다"))
        elif name == "cd":
            cwd = _after_cd(real, cwd)  # cd x && rm y 의 y 는 x 안에 있다
        elif name in DANGEROUS:
            findings.extend(_check_rm(real, wrappers, cwd))
        elif name == "git":
            findings.extend(_check_git(real, cwd))
        elif name == "find" and "-delete" in real:
            level = L3 if any(_is_critical(_resolve(w, cwd)) for w in _find_roots(real)) else L2
            findings.append(Finding(level, "find -delete: 찾은 파일을 삭제합니다. 되돌릴 수 없습니다"))
    return Verdict(list({f.message: f for f in findings}.values()))


def _check_git(real, cwd):
    sub, args, where = state.git_subcommand(real, cwd)
    if sub == "push":
        return gitpush.check(args, where)
    return gitwork.check(sub, args, where)


def _check_rm(real, wrappers, cwd):
    words = _operands(real)
    critical = []
    known = []
    unknown = "xargs" in wrappers or not words  # xargs rm 은 실행 전에 대상을 알 수 없다
    for word in words:
        target = _whole_dir(_resolve(_fill_vars(word), cwd)) if _fill_vars(word) else None
        if target and _is_critical(target):
            critical.append((target, CRITICAL_DIR))
        elif "$" in word or "{}" in word:  # rm $X, find -exec rm {}
            unknown = True
        else:
            known.append(word)

    committed = []
    lost = []
    for path in _expand(known, cwd):
        paths, items = state.inspect(path)
        committed.extend(paths)
        lost.extend(items)

    findings = []
    if unknown:
        findings.append(Finding(L2, "rm: 지울 대상을 확인할 수 없습니다. 삭제한 파일은 되돌릴 수 없습니다"))
    all_paths = committed + [path for path, _ in lost]
    if critical or lost:
        message = risk.describe("rm: 되살릴 수 없는 파일이 있습니다", critical + lost, cwd)
        if committed:
            message += f"\n    나머지 커밋된 파일 {len(set(committed))}개는 git restore 로 되살릴 수 있습니다"
        # 루트, 홈, 커밋 기록까지 지우는 경우는 영향 범위를 볼 것 없이 L3 이다
        if critical or any(reason in state.CRITICAL_REASONS for _, reason in lost):
            findings.append(Finding(L3, message))
        else:
            findings.append(risk.judge_files(R3, all_paths, message))
    elif committed:
        message = f"rm: 커밋된 파일 {len(set(committed))}개를 지웁니다. git restore 로 되살릴 수 있습니다"
        findings.append(risk.judge_files(R2, all_paths, message))
    return findings


def _fill_vars(word):
    """변수를 값으로 채운다. 셸처럼 없는 변수는 빈 문자열이 되므로 rm -rf $X/ 는 / 가 된다."""
    return re.sub(r"\$\{?(\w+)\}?", lambda m: os.environ.get(m.group(1), ""), word)


def _resolve(word, cwd):
    word = os.path.expanduser(word)
    return os.path.normpath(word if os.path.isabs(word) else os.path.join(cwd, word))


def _whole_dir(path):
    """폴더 안 전부를 뜻하는 경로(~/*, *)는 그 폴더 자체로 본다."""
    return os.path.dirname(path) if os.path.basename(path) == "*" else path


def _is_critical(path):
    home = os.path.realpath(os.path.expanduser("~"))
    real = os.path.realpath(path)
    is_home_or_above = real == home or home.startswith(real.rstrip(os.sep) + os.sep)
    return path in SYSTEM_DIRS or real in SYSTEM_DIRS or is_home_or_above


def _find_roots(real):
    """find 가 뒤지기 시작하는 경로들. 첫 옵션 앞까지이고, 없으면 현재 폴더다."""
    roots = []
    for word in real[1:]:
        if word.startswith(("-", "(", "!")):
            break
        roots.append(word)
    return roots or ["."]


def _operands(real):
    """rm 의 옵션과 리다이렉트를 뺀 대상만 남긴다."""
    words = []
    only_operands = False
    for word in parse.strip_redirects(real[1:]):
        if only_operands or not word.startswith("-"):
            words.append(word)
        elif word == "--":
            only_operands = True
    return words


def _expand(words, cwd):
    """글롭과 ~ 를 펼쳐 실제로 있는 경로만 돌려준다. 셸이 지우기 전에 미리 계산한다."""
    paths = []
    for word in words:
        word = os.path.expanduser(word)
        absolute = word if os.path.isabs(word) else os.path.join(cwd, word)
        if any(c in word for c in "*?["):
            base = word if os.path.isabs(word) else os.path.join(glob.escape(cwd), word)
            paths.extend(sorted(glob.glob(base)))
        elif os.path.lexists(absolute):
            paths.append(absolute)
    return paths


def _after_cd(real, cwd):
    args = real[1:]
    if not args:
        target = os.path.expanduser("~")
    elif args[0] == "-":
        target = os.environ.get("OLDPWD", cwd)
    else:
        target = os.path.join(cwd, os.path.expanduser(args[0]))
    return target if os.path.isdir(target) else cwd


def _walk(commands):
    """(sudo 등을 벗긴 진짜 명령, 벗겨낸 단어) 를 다른 명령 안에 들어 있는 것까지 차례로 내놓는다."""
    for argv in commands:
        real, wrappers = parse.unwrap(argv)
        if not real:
            continue
        yield real, wrappers
        inner = parse.inner_line(real)
        if inner is not None:
            try:
                yield from _walk(parse.parse(inner))
            except ValueError:
                yield ["$unparsed"], []
        if os.path.basename(real[0]) == "find":
            yield from _walk(parse.find_exec(real))
