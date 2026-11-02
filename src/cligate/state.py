import os
import signal
import subprocess
from dataclasses import dataclass
from functools import lru_cache

TIMEOUT = 2  # 초. 넘기면 "확인 실패" 로 보고 사용자에게 묻는다
NETWORK_TIMEOUT = 5
MAX_PULL_BRANCHES = 30
COMMIT_FORMAT = "--format=%h  %s  (%an, %ar)"
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_options_cache = {}
GIT_INTERNAL = "git 기록 자체"
WHOLE_REPO = "저장소 전체, .git 포함"
CRITICAL_REASONS = {GIT_INTERNAL, WHOLE_REPO}  # 커밋 기록까지 사라져 git 으로도 되살릴 수 없다


def inspect(path):
    """지우려는 경로를 git 과 대조한다. ([커밋되어 되살릴 수 있는 파일], [(되살릴 수 없는 것, 이유)]) 를 돌려준다."""
    path = os.path.abspath(path)
    if ".git" in path.split(os.sep):
        return [], [(path, GIT_INTERNAL)]

    is_dir = os.path.isdir(path) and not os.path.islink(path)
    where = path if is_dir else os.path.dirname(path)
    spec = "." if is_dir else os.path.basename(path)

    top = _git(["rev-parse", "--show-toplevel"], where)
    if top is None:
        return [], [(path, "git 기록 없음")]
    top = top.strip()
    if is_dir and os.path.realpath(top) == os.path.realpath(path):
        return [], [(path, WHOLE_REPO)]

    status = _git(["status", "--porcelain", "-z", "--untracked-files=all", "--ignored", "--", spec], where)
    tracked = _git(["ls-files", "-z", "--full-name", "--", spec], where)
    if status is None or tracked is None:
        return [], [(path, "상태 확인 실패")]

    lost, dirty = _read_status(status, top)
    committed = set(tracked.split("\0")) - dirty - {""}
    return [os.path.join(top, name) for name in sorted(committed)], lost


def _read_status(status, top):
    lost = []
    dirty = set()
    entries = iter(status.split("\0"))
    for entry in entries:
        if not entry:
            continue
        code, name = entry[:2], entry[3:]
        if code[0] in "RC":
            next(entries, None)  # 이름 변경은 원래 이름이 한 칸 더 온다
        dirty.add(name)
        if "D" in code:
            continue  # 이미 작업 폴더에 없으므로 rm 으로 잃을 것이 없다
        lost.append((os.path.join(top, name), _reason(code)))
    return lost, dirty


def _reason(code):
    if code == "??":
        return "git에 없음"
    if code == "!!":
        return "git이 무시하는 파일"
    return "커밋 안 한 변경"


@dataclass
class Snapshot:
    """자동완성의 상태 규칙에 쓰는 저장소 상태. 원격은 마지막 fetch 기준이다."""
    branch: str = None
    upstream: bool = False
    initial: bool = False
    staged: bool = False
    to_add: bool = False
    changed: bool = False
    untracked: bool = False
    conflicts: bool = False
    detached: bool = False
    rebasing: bool = False
    ahead: int = 0
    behind: int = 0


def snapshot(cwd):
    """저장소 상태를 읽는다. 저장소 밖이거나 확인하지 못하면 None."""
    out = _git(["status", "--porcelain=v2", "--branch"], cwd)
    if out is None:
        return None
    snap = Snapshot()
    for line in out.splitlines():
        if line == "# branch.head (detached)":
            snap.detached = True
        elif line.startswith("# branch.head "):
            snap.branch = line.split(" ", 2)[2]
        elif line == "# branch.oid (initial)":
            snap.initial = True
        elif line.startswith("# branch.upstream "):
            snap.upstream = True
        elif line.startswith("# branch.ab "):
            ahead, behind = line.split()[2:4]
            snap.ahead, snap.behind = int(ahead), abs(int(behind))
        elif line.startswith("u "):
            snap.conflicts = True
            snap.changed = True
            snap.to_add = True
        elif line.startswith(("1 ", "2 ")):
            snap.changed = True
            snap.staged = snap.staged or line[2] != "."
            snap.to_add = snap.to_add or line[3] != "."
        elif line.startswith("? "):
            snap.untracked = True
            snap.to_add = True
    paths = _git(["rev-parse", "--git-path", "rebase-merge", "--git-path", "rebase-apply"], cwd) or ""
    snap.rebasing = any(os.path.isdir(os.path.join(cwd, p)) for p in paths.splitlines())
    return snap


def work_counts(cwd):
    """(add 가 필요한 파일 수, 커밋을 기다리는 파일 수). 네트워크를 쓰지 않는다. 확인하지 못하면 (0, 0)."""
    out = _git(["status", "--porcelain=v2"], cwd)
    if out is None:
        return 0, 0
    to_add = staged = 0
    for line in out.splitlines():
        if line.startswith(("? ", "u ")):
            to_add += 1
        elif line.startswith(("1 ", "2 ")):
            to_add += line[3] != "."
            staged += line[2] != "."
    return to_add, staged


@lru_cache(maxsize=1)
def git_commands():
    out = _git(["--list-cmds=main"], os.getcwd())
    return out.splitlines() if out else []


def git_options(sub, cwd, negated=False):
    """git 이 자동완성용으로 내장한 옵션 목록. -- 뒤에 오는 --no-* 는 negated 일 때만 함께 돌려준다."""
    if sub not in _options_cache:
        out = _git([sub, "--git-completion-helper"], cwd)
        if not out:
            return []  # 저장소 밖에서는 실패할 수 있어 저장하지 않는다
        usual, _, others = out.partition(" -- ")
        _options_cache[sub] = usual.split(), others.split()
    usual, others = _options_cache[sub]
    return usual + others if negated else usual


def branches(cwd):
    out = _git(["for-each-ref", "--format=%(refname:short)", "refs/heads"], cwd)
    return out.splitlines() if out else []


def previous_branch(cwd):
    """git switch - 가 돌아갈 브랜치. 직전이 브랜치가 아니었으면 None."""
    name = _git(["rev-parse", "--abbrev-ref", "--verify", "-q", "@{-1}"], cwd)
    name = name.strip() if name else None
    return name if name in branches(cwd) else None


def merged_branches(cwd, current):
    """지금 브랜치에 병합되어 지워도 되는 브랜치들. 지금 브랜치와 main, master 는 뺀다."""
    out = _git(["for-each-ref", "--merged=HEAD", "--format=%(refname:short)", "refs/heads"], cwd)
    return [b for b in (out or "").splitlines() if b != current and b not in {"main", "master"}]


def has_parent(cwd):
    return _git(["rev-parse", "--verify", "-q", "HEAD~1^{commit}"], cwd) is not None


def has_stash(cwd):
    return _git(["rev-parse", "--verify", "-q", "refs/stash"], cwd) is not None


def unadded_outside(cwd):
    """add 가 필요한데 지금 폴더 바깥에 있는 파일을 현재 폴더 기준 경로로 돌려준다."""
    top = repo_root(cwd)
    status = _git(["status", "--porcelain", "-z", "--untracked-files=all"], cwd)
    if top is None or status is None:
        return []
    base = os.path.realpath(cwd)
    found = []
    entries = iter(status.split("\0"))
    for entry in entries:
        if not entry:
            continue
        code, name = entry[:2], entry[3:]
        if code[0] in "RC":
            next(entries, None)  # 이름 변경은 원래 이름이 한 칸 더 온다
        path = os.path.realpath(os.path.join(top, name))
        if code[1] != " " and not path.startswith(base + os.sep):
            found.append(os.path.relpath(path, base))
    return sorted(found)


def changed_paths(cwd, tracked_only=False):
    """바뀐 파일을 현재 폴더 기준 경로로 돌려준다. 현재 폴더 바깥은 뺀다."""
    top = repo_root(cwd)
    args = ["status", "--porcelain", "-z", "--untracked-files=no" if tracked_only else "--untracked-files=all"]
    status = _git(args, cwd)
    if top is None or status is None:
        return []
    base = os.path.realpath(cwd)
    names = (os.path.relpath(path, base) for path, _ in _read_status(status, top)[0])
    return sorted(n for n in names if not n.startswith(".."))


def remotes(cwd):
    out = _git(["remote"], cwd)
    return out.splitlines() if out else []


def path_commands():
    """PATH 에 있는 실행 파일 이름. PATH 가 그대로면 다시 읽지 않는다."""
    return _scan_path(os.environ.get("PATH", ""))


@lru_cache(maxsize=1)
def _scan_path(path):
    names = set()
    for folder in path.split(os.pathsep):
        try:
            names.update(e.name for e in os.scandir(folder) if os.access(e.path, os.X_OK))
        except OSError:
            continue
    return sorted(names)


def unrecoverable_names(folder):
    """폴더 바로 아래 이름 중 rm 하면 되살릴 수 없는 것. 저장소 밖이면 모두 같으므로 구별하지 않는다."""
    top = repo_root(folder)
    status = _git(["status", "--porcelain", "-z", "--untracked-files=all", "--ignored", "--", "."], folder)
    if top is None or status is None:
        return set()
    base = os.path.realpath(folder)
    return {os.path.relpath(path, base).split(os.sep)[0] for path, _ in _read_status(status, top)[0]}


def git_subcommand(real, cwd):
    """git -C x push ... 에서 (서브커맨드, 그 뒤의 인자, 실제 위치) 를 꺼낸다."""
    i = 1
    while i < len(real) and real[i].startswith("-"):
        if real[i] == "-C" and i + 1 < len(real):
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(real[i + 1])))
        i += 2 if real[i] in GIT_VALUE_OPTS else 1
    if i >= len(real):
        return None, [], cwd
    return real[i], real[i + 1:], cwd


def repo_root(cwd):
    top = _git(["rev-parse", "--show-toplevel"], cwd)
    return top.strip() if top else None


def uncommitted(cwd, paths=()):
    """HEAD 와 다른 추적 파일 [(경로, 이유)]. 지워진 파일은 되살아나므로 뺀다. 확인하지 못하면 None."""
    top = repo_root(cwd)
    status = _git(["status", "--porcelain", "-z", "--untracked-files=no", "--", *paths], cwd)
    if top is None or status is None:
        return None
    return _read_status(status, top)[0]


def unstaged(cwd, paths):
    """스테이지되지 않은 변경 [(경로, 이유)]. 확인하지 못하면 None."""
    top = repo_root(cwd)
    names = _git(["diff", "--name-only", "--diff-filter=d", "-z", "--", *paths], cwd)
    if top is None or names is None:
        return None
    return [(os.path.join(top, name), "커밋 안 한 변경") for name in names.split("\0") if name]


def dropped_commits(ref, cwd):
    """git reset --hard 로 지금 브랜치에서 빠질 커밋들."""
    out = _git(["log", COMMIT_FORMAT, f"{ref}..HEAD"], cwd)
    return out.splitlines() if out else []


def deleted_branch_commits(branch, cwd):
    """브랜치를 지울 때 (어디에도 안 남는 커밋, 원격 추적 브랜치에만 남는 커밋) 을 돌려준다. 확인하지 못하면 None."""
    ref = f"refs/heads/{branch}"
    # --exclude 는 바로 뒤 옵션에만 적용되고, --branches 앞에서는 refs/heads/ 없이 써야 한다. stash 는 남는 곳이 아니다
    lost = _git(["log", COMMIT_FORMAT, ref, "--not", f"--exclude={ref}", "--exclude=refs/stash", "--all"], cwd)
    not_local = _git(["log", COMMIT_FORMAT, ref, "--not", f"--exclude={branch}", "--branches"], cwd)
    if lost is None or not_local is None:
        return None
    lost = lost.splitlines()
    return lost, [c for c in not_local.splitlines() if c not in lost]


def stash_entries(cwd):
    """치워둔 변경 목록. 예: stash@{0}  WIP on main. 확인하지 못하면 None."""
    out = _git(["stash", "list", "--format=%gd  %gs"], cwd)
    return None if out is None else out.splitlines()


def stash_files(ref, cwd):
    out = _git(["stash", "show", "--name-only", ref], cwd)
    return out.splitlines() if out else []


def same_commit(ref, sha, cwd):
    out = _git(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], cwd)
    return out is not None and out.strip() == sha


def is_commit(ref, cwd):
    return _git(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], cwd) is not None


def clean_requires_force(cwd):
    return (_git(["config", "--type=bool", "--get", "clean.requireForce"], cwd) or "true").strip() == "true"


def clean_targets(args, cwd):
    """git clean 이 지울 경로를 -n 으로 미리 물어본다. 확인하지 못하면 None."""
    # -n 은 -f 가 같이 있어도 지우지 않는다
    out = _git(["-c", "core.quotePath=false", "clean", "-n", *args], cwd, extra_env={"LC_ALL": "C"})
    if out is None:
        return None
    base = os.path.realpath(cwd)
    prefix = "Would remove "
    return [os.path.join(base, line[len(prefix):].strip('"').rstrip("/"))
            for line in out.splitlines() if line.startswith(prefix)]


def current_branch(cwd):
    name = _git(["symbolic-ref", "--short", "-q", "HEAD"], cwd)
    return name.strip() if name else None


def push_remote(branch, cwd):
    for key in (f"branch.{branch}.pushRemote", "remote.pushDefault", f"branch.{branch}.remote"):
        value = _git(["config", "--get", key], cwd)
        if value:
            return value.strip()
    return "origin"


def remote_head(remote, branch, cwd):
    """원격 브랜치의 지금 커밋을 직접 묻는다. 없으면 "", 확인하지 못하면 None."""
    out = _git_network(["ls-remote", remote, f"refs/heads/{branch}"], cwd)
    return None if out is None else out.split("\t")[0].strip()


def tracked_head(remote, branch, cwd):
    """마지막 fetch 로 알고 있는 원격 브랜치의 커밋. 네트워크를 쓰지 않는다. 모르면 None."""
    out = _git(["rev-parse", "--verify", "-q", f"refs/remotes/{remote}/{branch}^{{commit}}"], cwd)
    return out.strip() if out else None


def upstream(branch, cwd):
    """브랜치가 따라가는 (원격, 원격 브랜치). 없거나 로컬 브랜치를 따라가면 None."""
    remote = _git(["config", "--get", f"branch.{branch}.remote"], cwd)
    merge = _git(["config", "--get", f"branch.{branch}.merge"], cwd)
    if not remote or not merge or remote.strip() == ".":
        return None
    return remote.strip(), merge.strip().removeprefix("refs/heads/")


def ahead_behind(sha, cwd):
    """(HEAD 에만 있는 커밋 수, sha 에만 있는 커밋 수). 확인하지 못하면 None."""
    out = _git(["rev-list", "--left-right", "--count", f"HEAD...{sha}"], cwd)
    if out is None:
        return None
    ahead, behind = out.split()
    return int(ahead), int(behind)


def pull_counts(remote, cwd):
    """원격 브랜치별로 pull 하면 받을 커밋 수 {브랜치: 수}. 마지막 fetch 기준이며 네트워크를 쓰지 않는다."""
    out = _git(["for-each-ref", "--count=" + str(MAX_PULL_BRANCHES), "--format=%(refname)", f"refs/remotes/{remote}"], cwd)
    prefix = f"refs/remotes/{remote}/"
    counts = {}
    for ref in (out or "").splitlines():
        if ref == f"{prefix}HEAD":
            continue
        count = _git(["rev-list", "--count", f"HEAD..{ref}"], cwd)
        if count:
            counts[ref[len(prefix):]] = int(count)
    return counts


def remote_tag(remote, tag, cwd):
    return bool(_git_network(["ls-remote", remote, f"refs/tags/{tag}"], cwd))


def remote_only_commits(remote, branch, local, remote_sha, cwd):
    """강제 푸시로 사라질 커밋 설명 목록. 확인하지 못하면 None."""
    if not ensure_commit(remote, branch, remote_sha, cwd):
        return None
    if _git(["rev-parse", "--verify", "-q", f"{local}^{{commit}}"], cwd) is None:
        return []  # 보낼 것이 없으면 git push 가 스스로 실패한다
    # --cherry-pick 은 rebase 로 내용이 그대로 옮겨진 내 커밋을 뺀다
    out = _git(["log", "--cherry-pick", "--right-only", COMMIT_FORMAT, f"{local}...{remote_sha}"], cwd)
    return None if out is None else out.splitlines()


def unpulled_commits(remote, branch, local, remote_sha, cwd):
    """원격에는 있고 local 에는 없는 커밋. 하나라도 있으면 평범한 push 는 거부된다. 확인하지 못하면 빈 목록."""
    if not ensure_commit(remote, branch, remote_sha, cwd) or not is_commit(local, cwd):
        return []
    out = _git(["log", COMMIT_FORMAT, f"{local}..{remote_sha}"], cwd)
    return out.splitlines() if out else []


def deleted_commits(remote, branch, remote_sha, cwd):
    """원격 브랜치를 지울 때 (어디에도 안 남는 커밋, 이 컴퓨터에만 남는 커밋) 을 돌려준다. 확인하지 못하면 None."""
    if not ensure_commit(remote, branch, remote_sha, cwd):
        return None
    # 지워질 브랜치 자신의 추적 정보는 남는 곳으로 치지 않는다.
    # --exclude 는 바로 뒤 옵션에만 적용되고, --remotes 앞에서는 refs/remotes/ 없이 써야 한다
    lost = _git(["log", COMMIT_FORMAT, remote_sha, "--not", f"--exclude=refs/remotes/{remote}/{branch}", "--all"], cwd)
    not_on_remote = _git(["log", COMMIT_FORMAT, remote_sha, "--not", f"--exclude={remote}/{branch}", "--remotes"], cwd)
    if lost is None or not_on_remote is None:
        return None
    lost = lost.splitlines()
    return lost, [commit for commit in not_on_remote.splitlines() if commit not in lost]


def ensure_commit(remote, branch, sha, cwd):
    if _has_commit(sha, cwd):
        return True
    # --refmap= 은 origin/main 을 갱신하지 않는다. 갱신하면 --force-with-lease 가
    # 아직 보지 못한 원격 변경을 본 것으로 착각해 보호 기능을 잃는다.
    fetched = _git_network(["fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "--refmap=", remote,
                            f"refs/heads/{branch}"], cwd)
    return fetched is not None and _has_commit(sha, cwd)


def _has_commit(sha, cwd):
    return _git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd) is not None


def _git_network(args, cwd):
    extra = {"GCM_INTERACTIVE": "never"}  # 로그인 창을 띄우지 않는다
    # 사용자가 ssh 를 직접 설정했다면 그 설정(다른 키 등)을 덮어쓰지 않는다
    if not {"GIT_SSH", "GIT_SSH_COMMAND"} & set(os.environ) and not _git(["config", "--get", "core.sshCommand"], cwd):
        extra["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes -o ConnectTimeout=3"  # 비밀번호를 묻지 않게
    return _git(args, cwd, NETWORK_TIMEOUT, extra)


def _git(args, cwd, timeout=TIMEOUT, extra_env=None):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", **(extra_env or {}))
    try:
        # subprocess.run 의 timeout 은 git 만 죽이고 그 자식 ssh 가 끝나길 기다린다. 그룹째 끝내야 제시간에 돌아온다
        proc = subprocess.Popen(["git", *args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                encoding="utf-8", errors="replace", start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    finally:
        if proc.poll() is None:  # 시간 초과나 Ctrl-C
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
    return out if proc.returncode == 0 else None
