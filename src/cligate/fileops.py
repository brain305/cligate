"""이미 있는 파일을 조용히 덮어쓰는 mv, cp, > 를 판정한다. 덮어쓸 것이 없으면 아무것도 묻지 않는다."""
import os

from . import parse, paths, risk, state
from .risk import Finding, L2, L3, R2, R3

VALUE_OPTS = {"-t", "--target-directory", "-S", "--suffix"}
TRUNCATING = {">", ">|", "&>", ">&"}  # >> 는 뒤에 덧붙이기만 하므로 잃을 것이 없다
DEVICE_PREFIX = "/dev/"


def check_copy(real, wrappers, cwd):
    """mv 와 cp 를 판정한다. 대상이 폴더면 그 안의 같은 이름 파일을 덮어쓰게 된다."""
    name = os.path.basename(real[0])
    flags, words, target = _read(real)
    # -n 은 덮어쓰지 않고, -i 는 명령이 직접 묻고, -b 는 백업을 남긴다
    if _has(flags, "n", "--no-clobber") or _has(flags, "i", "--interactive") or _has(flags, "b", "--backup"):
        return []
    if "xargs" in wrappers:
        return [Finding(L2, f"{name}: 덮어쓸 대상을 확인할 수 없습니다")]

    findings = []
    if name == "mv":
        findings.extend(_moving_critical(words if target else words[:-1], cwd))
    if target is None:
        if len(words) < 2:
            return findings  # 대상이 없으면 명령이 스스로 실패한다
        target = words[-1]
        words = words[:-1]

    known_target = _known(target)
    if known_target is None:
        return [*findings, Finding(L2, f"{name}: 덮어쓸 대상을 확인할 수 없습니다")]
    dest = paths.resolve(known_target, cwd)
    into_dir = os.path.isdir(dest) and not _has(flags, "T", "--no-target-directory")

    known = [w for w in map(_known, words) if w is not None]
    if len(known) < len(words) and into_dir:
        return [*findings, Finding(L2, f"{name}: 덮어쓸 대상을 확인할 수 없습니다")]
    recursive = name == "cp" and (_has(flags, "r", "--recursive") or _has(flags, "R", "--recursive")
                                  or _has(flags, "a", "--archive"))
    if len(known) < len(words) and os.path.isfile(dest):
        return [*findings, *_judge(name, [dest], cwd)]  # 원본은 몰라도 덮어쓸 파일은 안다

    targets = []
    for source in paths.expand(known, cwd):
        targets.extend(_overwritten(source, dest, into_dir, recursive))
    return [*findings, *_judge(name, targets, cwd)]


def check_redirects(real, cwd):
    """> 파일 을 판정한다. 파일 내용이 명령이 실행되기도 전에 비워진다."""
    findings = []
    for i, word in enumerate(real[:-1]):
        if word not in TRUNCATING:
            continue
        target = real[i + 1]
        if target.isdigit() or target == "-":
            continue  # 2>&1, >&- 는 파일이 아니다
        known = _known(target)
        if known is None:
            findings.append(Finding(L2, f"{word}: 덮어쓸 파일을 확인할 수 없습니다"))
            continue
        path = paths.resolve(known, cwd)
        if not path.startswith(DEVICE_PREFIX) and os.path.isfile(path):
            findings.extend(_judge(word, [path], cwd))
    return findings


def _moving_critical(sources, cwd):
    found = []
    for word in sources:
        known = _known(word)
        if known is None:
            continue
        path = paths.resolve(known, cwd)
        if os.path.lexists(path) and (paths.is_critical(path) or os.path.basename(path) == ".git"):
            found.append(Finding(L2, f"mv: 중요한 폴더를 옮깁니다  {path}"))
    return found


def _overwritten(source, dest, into_dir, recursive):
    if not os.path.isdir(source) or os.path.islink(source):
        target = os.path.join(dest, os.path.basename(source)) if into_dir else dest
        if os.path.lexists(target) and not os.path.isdir(target) and not _same(source, target):
            return [target]
        return []
    if not recursive:
        return []  # mv 는 비어 있지 않은 폴더를 덮어쓰지 못하고, -r 없는 cp 는 폴더를 복사하지 못한다
    base = os.path.join(dest, os.path.basename(source)) if into_dir else dest
    found = []
    for root, _, names in os.walk(source):
        for name in names:
            target = os.path.join(base, os.path.relpath(root, source), name)
            if os.path.lexists(target) and not _same(os.path.join(root, name), target):
                found.append(os.path.normpath(target))
                if len(found) >= risk.MAX_FILES:
                    return found  # 영향 범위를 정하는 데 이만큼이면 충분하다
    return found


def _judge(name, targets, cwd):
    targets = [t for t in dict.fromkeys(targets) if _has_content(t)]
    committed = []
    lost = []
    for path in targets[:risk.MAX_FILES]:
        files, items = state.inspect(path)
        committed.extend(files)
        lost.extend(items)
    all_paths = committed + [path for path, _ in lost]
    if lost:
        message = risk.describe(f"{name}: 이미 있는 파일을 덮어씁니다. 되살릴 수 없습니다", lost, cwd)
        if committed:
            message += f"\n    나머지 커밋된 파일 {len(committed)}개는 git restore 로 되살릴 수 있습니다"
        if any(reason in state.CRITICAL_REASONS for _, reason in lost):
            return [Finding(L3, message)]
        return [risk.judge_files(R3, all_paths, message)]
    if committed:
        return [risk.judge_files(R2, committed, f"{name}: 커밋된 파일 {len(committed)}개를 덮어씁니다. git restore 로 되살릴 수 있습니다")]
    return []


def _read(real):
    flags, words = [], []
    target = None
    only_operands = False
    args = iter(parse.strip_redirects(real[1:]))
    for word in args:
        if only_operands or not word.startswith("-") or word == "-":
            words.append(word)
        elif word == "--":
            only_operands = True
        elif word in VALUE_OPTS:
            value = next(args, None)
            target = value if word in ("-t", "--target-directory") else target
        elif word.startswith("--target-directory="):
            target = word.split("=", 1)[1]
        else:
            flags.append(word)
    return flags, words, target


def _has(flags, short, long):
    return any(f == long or f.startswith(f"{long}=") or (not f.startswith("--") and short in f) for f in flags)


def _known(word):
    """실행 전에 값을 알 수 있으면 그 값을, 모르면 None 을 돌려준다."""
    if parse.SUBSTITUTION in word or "{}" in word:
        return None
    return paths.fill_vars(word) or None


def _same(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _has_content(path):
    try:
        return os.path.islink(path) or os.path.getsize(path) > 0  # 빈 파일은 덮어써도 잃을 것이 없다
    except OSError:
        return False
