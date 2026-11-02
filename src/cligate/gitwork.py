from . import parse, risk, state
from .risk import Finding, L1, L2, R2, R3, S1

CHECKOUT_VALUE_OPTS = {"-b", "-B", "--orphan"}
RESTORE_VALUE_OPTS = {"-s", "--source"}
CLEAN_VALUE_OPTS = {"-e", "--exclude"}


def check(sub, args, cwd):
    """작업 폴더의 변경을 날리는 git 명령을 판정한다. 저장소 밖이면 git 이 알아서 거부한다."""
    handler = {"reset": _reset, "clean": _clean, "checkout": _checkout, "restore": _restore}.get(sub)
    if handler is None or state.repo_root(cwd) is None:
        return []
    return handler(parse.strip_redirects(args), cwd)


def _reset(args, cwd):
    if "--hard" not in args:
        return []
    _, words, _ = _split(args, set())
    ref = words[0] if words else "HEAD"
    findings = _lost_work("git reset --hard", state.uncommitted(cwd), cwd)
    if "$" in ref:
        findings.append(Finding(L2, "git reset --hard: 어느 커밋으로 옮기는지 확인할 수 없습니다"))
        return findings
    commits = state.dropped_commits(ref, cwd)
    if commits:
        message = f"git reset --hard: 커밋 {len(commits)}개가 브랜치에서 빠집니다. git reflog 로 되찾을 수 있습니다"
        findings.append(risk.judge(R2, S1, message))
    return findings


def _clean(args, cwd):
    flags, words, paths = _split(args, CLEAN_VALUE_OPTS)
    if _has(flags, "n", "--dry-run") or _has(flags, "i", "--interactive"):
        return []
    if not _has(flags, "f", "--force") and state.clean_requires_force(cwd):
        return []  # -f 가 없으면 git 이 지우기를 거부한다
    if any("$" in w for w in words + paths):
        return [Finding(L2, "git clean: 지울 대상을 확인할 수 없습니다")]

    targets = state.clean_targets(args, cwd)
    if targets is None:
        return [Finding(L2, "git clean: 상태를 확인하지 못했습니다")]
    scope, _ = risk.measure(targets)
    if scope == risk.S0:
        return []  # 빈 폴더뿐이면 잃을 것이 없다
    title = "git clean: 커밋되지 않은 파일을 지웁니다. 되살릴 수 없습니다"
    return [risk.judge_files(R3, targets, risk.describe(title, [(t, "git에 없음") for t in targets], cwd))]


def _checkout(args, cwd):
    flags, words, paths = _split(args, CHECKOUT_VALUE_OPTS)
    if _has(flags, "p", "--patch"):
        return []
    if _from_file(args):
        return [Finding(L2, "git checkout: 대상을 파일에서 읽어 확인할 수 없습니다")]

    name = "git checkout"
    if "--" in args:
        ref = words[0] if words else None
    elif any("$" in w for w in words):
        return [Finding(L1, f"{name}: 브랜치인지 파일인지 실행 전에는 알 수 없어 확인하지 못했습니다")]
    elif words and state.is_commit(words[0], cwd):
        ref, paths = words[0], words[1:]
    else:
        ref, paths = None, words

    if not paths:  # 브랜치 이동. 변경이 충돌하면 git 이 거부하지만 -f 는 그대로 덮어쓴다
        return _lost_work(f"{name} -f", state.uncommitted(cwd), cwd) if _has(flags, "f", "--force") else []
    if any("$" in p for p in paths):
        return [Finding(L2, f"{name}: 되돌릴 파일을 확인할 수 없습니다")]
    # 커밋을 지정하면 스테이지된 변경까지, 아니면 스테이지되지 않은 변경만 덮어쓴다
    return _lost_work(name, state.uncommitted(cwd, paths) if ref else state.unstaged(cwd, paths), cwd)


def _restore(args, cwd):
    flags, words, paths = _split(args, RESTORE_VALUE_OPTS)
    paths = words + paths
    if _has(flags, "p", "--patch"):
        return []
    if _from_file(args):
        return [Finding(L2, "git restore: 대상을 파일에서 읽어 확인할 수 없습니다")]
    if not paths:
        return []  # 경로가 없으면 git 이 거부한다

    staged = _has(flags, "S", "--staged")
    if staged and not _has(flags, "W", "--worktree"):
        return []  # 스테이지만 풀고 작업 폴더의 파일은 그대로 둔다
    if any("$" in p for p in paths):
        return [Finding(L2, "git restore: 되돌릴 파일을 확인할 수 없습니다")]
    return _lost_work("git restore", state.uncommitted(cwd, paths) if staged else state.unstaged(cwd, paths), cwd)


def _lost_work(name, items, cwd):
    if items is None:
        return [Finding(L2, f"{name}: 상태를 확인하지 못했습니다")]
    if not items:
        return []
    title = f"{name}: 커밋 안 한 변경을 잃습니다. 되살릴 수 없습니다"
    return [risk.judge_files(R3, [path for path, _ in items], risk.describe(title, items, cwd))]


def _split(args, value_opts):
    """(옵션, -- 앞의 단어, -- 뒤의 단어) 로 나눈다."""
    flags, words = [], []
    skip_next = False
    for i, word in enumerate(args):
        if skip_next:
            skip_next = False
        elif word == "--":
            return flags, words, args[i + 1:]
        elif word in value_opts:
            skip_next = True
        elif word.startswith("-"):
            flags.append(word)
        else:
            words.append(word)
    return flags, words, []


def _has(flags, short, long):
    return long in flags or any(not f.startswith("--") and short in f for f in flags)


def _from_file(args):
    return any(a.startswith("--pathspec-from-file") for a in args)
