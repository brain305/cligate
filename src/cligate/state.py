import os
import signal
import subprocess

TIMEOUT = 2  # 초. 넘기면 "확인 실패" 로 보고 사용자에게 묻는다
NETWORK_TIMEOUT = 5
COMMIT_FORMAT = "--format=%h  %s  (%an, %ar)"
GIT_INTERNAL = "git 기록 자체"
WHOLE_REPO = "저장소 전체, .git 포함"
CRITICAL_REASONS = {GIT_INTERNAL, WHOLE_REPO}  # 커밋 기록까지 사라져 git 으로도 되살릴 수 없다


def inspect(path):
    """지우려는 경로를 git 과 대조한다. (커밋되어 되살릴 수 있는 파일 수, [(되살릴 수 없는 것, 이유)]) 를 돌려준다."""
    path = os.path.abspath(path)
    if ".git" in path.split(os.sep):
        return 0, [(path, GIT_INTERNAL)]

    is_dir = os.path.isdir(path) and not os.path.islink(path)
    where = path if is_dir else os.path.dirname(path)
    spec = "." if is_dir else os.path.basename(path)

    top = _git(["rev-parse", "--show-toplevel"], where)
    if top is None:
        return 0, [(path, "git 기록 없음")]
    top = top.strip()
    if is_dir and os.path.realpath(top) == os.path.realpath(path):
        return 0, [(path, WHOLE_REPO)]

    status = _git(["status", "--porcelain", "-z", "--untracked-files=all", "--ignored", "--", spec], where)
    tracked = _git(["ls-files", "-z", "--full-name", "--", spec], where)
    if status is None or tracked is None:
        return 0, [(path, "상태 확인 실패")]

    lost, dirty = _read_status(status, top)
    committed = set(tracked.split("\0")) - dirty - {""}
    return len(committed), lost


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


def join(cwd, path):
    return os.path.normpath(os.path.join(cwd, os.path.expanduser(path)))


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


def remote_tag(remote, tag, cwd):
    return bool(_git_network(["ls-remote", remote, f"refs/tags/{tag}"], cwd))


def remote_only_commits(remote, branch, local, remote_sha, cwd):
    """강제 푸시로 사라질 커밋 설명 목록. 확인하지 못하면 None."""
    if not _ensure_commit(remote, branch, remote_sha, cwd):
        return None
    if _git(["rev-parse", "--verify", "-q", f"{local}^{{commit}}"], cwd) is None:
        return []  # 보낼 것이 없으면 git push 가 스스로 실패한다
    # --cherry-pick 은 rebase 로 내용이 그대로 옮겨진 내 커밋을 뺀다
    out = _git(["log", "--cherry-pick", "--right-only", COMMIT_FORMAT, f"{local}...{remote_sha}"], cwd)
    return None if out is None else out.splitlines()


def deleted_commits(remote, branch, remote_sha, cwd):
    """원격 브랜치를 지울 때 (어디에도 안 남는 커밋, 이 컴퓨터에만 남는 커밋) 을 돌려준다. 확인하지 못하면 None."""
    if not _ensure_commit(remote, branch, remote_sha, cwd):
        return None
    # 지워질 브랜치 자신의 추적 정보는 남는 곳으로 치지 않는다.
    # --exclude 는 바로 뒤 옵션에만 적용되고, --remotes 앞에서는 refs/remotes/ 없이 써야 한다
    lost = _git(["log", COMMIT_FORMAT, remote_sha, "--not", f"--exclude=refs/remotes/{remote}/{branch}", "--all"], cwd)
    not_on_remote = _git(["log", COMMIT_FORMAT, remote_sha, "--not", f"--exclude={remote}/{branch}", "--remotes"], cwd)
    if lost is None or not_on_remote is None:
        return None
    lost = lost.splitlines()
    return lost, [commit for commit in not_on_remote.splitlines() if commit not in lost]


def _ensure_commit(remote, branch, sha, cwd):
    if _has_commit(sha, cwd):
        return True
    # --refmap= 은 origin/main 을 갱신하지 않는다. 갱신하면 --force-with-lease 가
    # 아직 보지 못한 원격 변경을 본 것으로 착각해 보호 기능을 잃는다.
    fetched = _git_network(["fetch", "--quiet", "--no-tags", "--refmap=", remote, f"refs/heads/{branch}"], cwd)
    return fetched is not None and _has_commit(sha, cwd)


def _has_commit(sha, cwd):
    return _git(["cat-file", "-e", f"{sha}^{{commit}}"], cwd) is not None


def _git_network(args, cwd):
    extra = {}
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
