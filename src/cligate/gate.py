import os

from . import APP_NAME, fileops, gitpush, gitwork, parse, paths, risk, shell, state
from .risk import Finding, L1, L2, L3, R2, R3, Verdict

DANGEROUS = {"rm"}
COPY_COMMANDS = {"mv", "cp"}
REMOTE_COMMANDS = {"push", "pull", "fetch"}
CRITICAL_DIR = "루트·홈·시스템 폴더"


def is_dangerous(line):
    parts = line.split()
    return bool(parts) and parts[0] in DANGEROUS


def preview(line):
    """입력 중인 줄의 등급을 미리 본다. 자동완성용이므로 네트워크를 쓰지 않고, 아직 덜 쓴 줄도 받는다."""
    try:
        return check(parse.parse(line), offline=True)
    except ValueError:  # 따옴표를 아직 닫지 않은 줄
        return Verdict()


def check(commands, offline=False):
    """한 줄을 판정해 Verdict 를 돌려준다. 등급은 명령과 지금 상태의 조합으로 정한다."""
    findings = []
    cwd = os.getcwd()
    # 입력 중에는 별칭을 읽느라 기다리지 않는다. 실행 전 판정은 다 읽은 뒤에 한다
    for real, wrappers in _walk(commands, shell.aliases(wait=not offline)):
        name = os.path.basename(real[0])
        findings.extend(fileops.check_redirects(real, cwd))
        if real[0].startswith("$"):
            findings.append(Finding(L1, f"{APP_NAME}: 실행 전에는 무슨 명령인지 알 수 없어 확인하지 못했습니다"))
        elif name == "cd":
            cwd = _after_cd(real, cwd)  # cd x && rm y 의 y 는 x 안에 있다
        elif name in DANGEROUS:
            findings.extend(_check_rm(real, wrappers, cwd))
        elif name in COPY_COMMANDS:
            findings.extend(fileops.check_copy(real, wrappers, cwd))
        elif name == "git":
            findings.extend(_check_git(real, cwd, offline))
        elif name == "find" and "-delete" in real:
            level = L3 if any(paths.is_critical(paths.resolve(w, cwd)) for w in _find_roots(real)) else L2
            findings.append(Finding(level, "find -delete: 찾은 파일을 삭제합니다. 되돌릴 수 없습니다"))
    return Verdict(list({f.message: f for f in findings}.values()))


def touches_remote(commands):
    """원격과 주고받는 git 명령이 있으면 True. 별칭 안에 있어도 찾는다."""
    for real, _ in _walk(commands, shell.aliases(wait=False)):
        if os.path.basename(real[0]) == "git" and state.git_subcommand(real, os.getcwd())[0] in REMOTE_COMMANDS:
            return True
    return False


def _check_git(real, cwd, offline):
    sub, args, where = state.git_subcommand(real, cwd)
    if sub == "push":
        return gitpush.check(args, where, offline)
    return gitwork.check(sub, args, where)


def _check_rm(real, wrappers, cwd):
    words = _operands(real)
    critical = []
    known = []
    # xargs rm 은 실행 전에 대상을 알 수 없다. 대상 없는 rm 은 아무것도 지우지 않는다
    unknown = "xargs" in wrappers
    for word in words:
        filled = paths.fill_vars(word)
        target = _whole_dir(paths.resolve(filled, cwd)) if filled else None
        if target and paths.is_critical(target):
            critical.append((target, CRITICAL_DIR))
        elif "$" in word or "{}" in word:  # rm $X, find -exec rm {}
            unknown = True
        else:
            known.append(word)

    committed = []
    lost = []
    expanded = paths.expand(known, cwd)
    for path in expanded[:risk.MAX_FILES]:  # 경계만 넘으면 등급이 정해지므로 전부 조사해 기다리게 하지 않는다
        files, items = state.inspect(path)
        committed.extend(files)
        lost.extend(items)

    findings = []
    if unknown:
        findings.append(Finding(L2, "rm: 지울 대상을 확인할 수 없습니다. 삭제한 파일은 되돌릴 수 없습니다"))
    all_paths = committed + [path for path, _ in lost] + expanded[risk.MAX_FILES:]
    unchecked = f"\n    파일이 많아 앞 {risk.MAX_FILES}개만 확인했습니다" if len(expanded) > risk.MAX_FILES else ""
    if critical or lost:
        message = risk.describe("rm: 되살릴 수 없는 파일이 있습니다", critical + lost, cwd)
        if committed:
            message += f"\n    나머지 커밋된 파일 {len(set(committed))}개는 git restore 로 되살릴 수 있습니다"
        # 루트, 홈, 커밋 기록까지 지우는 경우는 영향 범위를 볼 것 없이 L3 이다
        if critical or any(reason in state.CRITICAL_REASONS for _, reason in lost):
            findings.append(Finding(L3, message + unchecked))
        else:
            findings.append(risk.judge_files(R3, all_paths, message + unchecked))
    elif committed:
        message = f"rm: 커밋된 파일 {len(set(committed))}개를 지웁니다. git restore 로 되살릴 수 있습니다"
        findings.append(risk.judge_files(R2, all_paths, message + unchecked))
    return findings


def _whole_dir(path):
    """폴더 안 전부를 뜻하는 경로(~/*, *)는 그 폴더 자체로 본다."""
    return os.path.dirname(path) if os.path.basename(path) == "*" else path


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


def _after_cd(real, cwd):
    args = real[1:]
    if not args:
        target = os.path.expanduser("~")
    elif args[0] == "-":
        target = os.environ.get("OLDPWD", cwd)
    else:
        target = os.path.join(cwd, os.path.expanduser(args[0]))
    return target if os.path.isdir(target) else cwd


def _walk(commands, aliases, seen=frozenset()):
    """(sudo 등을 벗긴 진짜 명령, 벗겨낸 단어) 를 다른 명령 안에 들어 있는 것까지 차례로 내놓는다."""
    for argv in commands:
        real, wrappers = parse.unwrap(argv)
        if not real:
            continue
        # sudo 뒤의 단어는 셸이 별칭으로 펼치지 않는다. seen 은 alias ls='ls -G' 와 순환을 막는다
        if real[0] in aliases and real[0] not in seen and not parse.WRAPPERS.keys() & set(wrappers):
            try:
                expanded = parse.parse(aliases[real[0]])
            except ValueError:
                yield ["$unparsed"], []
                continue
            if expanded:
                expanded[-1] = expanded[-1] + real[1:]
            yield from _walk(expanded, aliases, seen | {real[0]})
            continue
        yield real, wrappers
        inner = parse.inner_line(real)
        if inner is not None:
            try:
                yield from _walk(parse.parse(inner), aliases, seen)
            except ValueError:
                yield ["$unparsed"], []
        if os.path.basename(real[0]) == "find":
            yield from _walk(parse.find_exec(real), aliases, seen)
