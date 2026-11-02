import glob
import os
import re

from . import gitpush, parse, state

DANGEROUS = {"rm"}
MAX_LISTED = 8
CONFIRM_WORD = "delete"
SYSTEM_DIRS = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/opt", "/sbin", "/usr", "/var",
               "/Applications", "/Library", "/System", "/Users"}
CRITICAL_DIR = "루트·홈·시스템 폴더"


def is_dangerous(line):
    parts = line.split()
    return bool(parts) and parts[0] in DANGEROUS


def check(commands):
    """(알리기만 할 문구, 확인받을 문구, 입력해야 할 단어) 를 돌려준다. 단어가 None 이면 y/N 으로 묻는다."""
    notices = []
    warnings = []
    word = None
    cwd = os.getcwd()
    for real, wrappers in _walk(commands):
        name = os.path.basename(real[0])
        if name == "cd":
            cwd = _after_cd(real, cwd)  # cd x && rm y 의 y 는 x 안에 있다
        elif name in DANGEROUS:
            if _check_rm(real, wrappers, cwd, notices, warnings):
                word = CONFIRM_WORD
        elif name == "git":
            notice, warning, push_word = gitpush.check(real, cwd)
            if notice:
                notices.append(notice)
            if warning:
                warnings.append(warning)
            word = word or push_word
        elif name == "find" and "-delete" in real:
            warnings.append("find -delete: 찾은 파일을 삭제합니다. 되돌릴 수 없습니다")
            if any(_is_critical(_resolve(w, cwd)) for w in _find_roots(real)):
                word = CONFIRM_WORD
    return _unique(notices), _unique(warnings), word


def unclear(commands):
    """실행 전에는 무슨 명령인지 알 수 없으면 True. 막지 않고 알리기만 한다."""
    return any(real[0].startswith("$") for real, _ in _walk(commands))


def _check_rm(real, wrappers, cwd, notices, warnings):
    """rm 을 판정한다. 단어 입력이 필요할 만큼 위험하면 True 를 돌려준다."""
    words = _operands(real)
    lost = []
    known = []
    unknown = "xargs" in wrappers or not words  # xargs rm 은 실행 전에 대상을 알 수 없다
    for word in words:
        target = _whole_dir(_resolve(_fill_vars(word), cwd)) if _fill_vars(word) else None
        if target and _is_critical(target):
            lost.append((target, CRITICAL_DIR))
        elif "$" in word or "{}" in word:  # rm $X, find -exec rm {}
            unknown = True
        else:
            known.append(word)

    committed = 0
    for path in _expand(known, cwd):
        count, items = state.inspect(path)
        committed += count
        lost.extend(items)

    if unknown:
        warnings.append("rm: 지울 대상을 확인할 수 없습니다. 삭제한 파일은 되돌릴 수 없습니다")
    if lost:
        warnings.append(_describe_lost(lost, committed, cwd))
    elif committed:
        notices.append(f"rm: 커밋된 파일 {committed}개를 지웁니다. git restore 로 되살릴 수 있습니다")
    return any(reason == CRITICAL_DIR or reason in state.CRITICAL_REASONS for _, reason in lost)


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
            paths.extend(glob.glob(base))
        elif os.path.lexists(absolute):
            paths.append(absolute)
    return paths


def _describe_lost(lost, committed, cwd):
    lines = ["rm: 되살릴 수 없는 파일이 있습니다"]
    for path, reason in lost[:MAX_LISTED]:
        shown = os.path.relpath(path, os.path.realpath(cwd))
        if shown.startswith(".."):
            shown = os.path.normpath(path)  # 현재 폴더 바깥은 전체 경로가 읽기 쉽다
        lines.append(f"    {shown}  ({reason})")
    if len(lost) > MAX_LISTED:
        lines.append(f"    ... 외 {len(lost) - MAX_LISTED}개")
    if committed:
        lines.append(f"    나머지 커밋된 파일 {committed}개는 git restore 로 되살릴 수 있습니다")
    return "\n".join(lines)


def _after_cd(real, cwd):
    args = real[1:]
    if not args:
        target = os.path.expanduser("~")
    elif args[0] == "-":
        target = os.environ.get("OLDPWD", cwd)
    else:
        target = os.path.join(cwd, os.path.expanduser(args[0]))
    return target if os.path.isdir(target) else cwd


def _unique(items):
    return list(dict.fromkeys(items))


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
