"""입력창 바로 아래에 원격 상태를 보여준다. 조사는 백그라운드에서 하므로 입력을 막지 않는다.

평범한 git fetch 를 쓰지 않는다. origin/main 이 갱신되면 --force-with-lease 가 아직 보지 못한 팀원
커밋을 본 것으로 착각해 덮어쓸 수 있기 때문이다. 원격에 직접 물어 추적 정보를 그대로 둔다.
"""
import os
import threading
import time
from dataclasses import dataclass, replace

from . import state

CHECK_SECONDS = 60
RETRY_SECONDS = (120, 300)  # 실패가 이어지면 간격을 늘린다
REDRAW_SECONDS = 5  # "원격 기준 8초 전" 을 갱신하는 간격

UP_TO_DATE, BEHIND, AHEAD, DIVERGED, NO_UPSTREAM, REMOTE_GONE, FAILED = (
    "up_to_date", "behind", "ahead", "diverged", "no_upstream", "remote_gone", "failed")
COMPARED = (UP_TO_DATE, BEHIND, AHEAD, DIVERGED)  # 원격과 비교해 앞섬과 뒤처짐을 센 상태

_current = None


@dataclass(frozen=True)
class Status:
    cwd: str
    branch: str
    kind: str
    ahead: int = 0
    behind: int = 0
    checked_at: float = 0.0
    to_add: int = 0
    staged: int = 0


def latest(cwd):
    """지금 폴더의 마지막 원격 상태. 입력 중에 읽으므로 기다리지 않는다. 모르면 None."""
    monitor = _current
    status = monitor.status if monitor else None
    return status if status and status.cwd == os.path.realpath(cwd) else None


def describe(status):
    """(문구, 색) 을 돌려준다."""
    if status.kind == UP_TO_DATE:
        return "✓ 최신", "ansigreen"
    if status.kind == BEHIND:
        return f"↓{status.behind} pull 필요", "ansiyellow"
    if status.kind == AHEAD:
        return f"↑{status.ahead} push 대기", "ansicyan"
    if status.kind == DIVERGED:
        return f"↓{status.behind} ↑{status.ahead} 갈라짐", "ansired"
    if status.kind == NO_UPSTREAM:
        return "원격 브랜치 없음", "ansibrightblack"
    if status.kind == REMOTE_GONE:
        return "원격에서 지워짐", "ansiyellow"
    return "? 원격 확인 안 됨", "ansibrightblack"


def _age(seconds):
    return f"{int(seconds)}초 전" if seconds < 60 else f"{int(seconds // 60)}분 전"


class Monitor:
    def __init__(self, interval=CHECK_SECONDS, retries=RETRY_SECONDS, clock=time.monotonic):
        self.status = None
        self.interval = interval
        self.retries = retries
        self.clock = clock
        self.on_change = None
        self.failures = 0
        self._paused = False
        self._stopped = False
        self._wake = threading.Event()
        self._settled = threading.Event()
        self._settled.set()
        self._refresh_remote = False
        self._next_network = 0.0
        self._known = {}  # (폴더, 원격, 브랜치) 별 마지막 원격 커밋. 명령 뒤에는 네트워크 없이 다시 센다

    def start(self):
        global _current
        _current = self
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def stop(self):
        self._stopped = True
        self._wake.set()

    def pause(self):
        """명령이 실행되는 동안에는 조사하지 않는다. git 명령과 겹치지 않게 하기 위해서이다."""
        self._paused = True

    def resume(self, refresh_remote=False):
        """명령이 끝나면 다시 조사한다. push, pull 뒤에는 원격이 바뀌었으므로 바로 다시 묻는다."""
        if refresh_remote:
            self._settled.clear()
            self._refresh_remote = True
        self._paused = False
        self._wake.set()

    def wait_settled(self, timeout):
        """바로 끝나면 다음 입력창부터 맞는 값을 보여준다. 느려도 입력은 막지 않는다."""
        return self._settled.wait(timeout)

    def status_parts(self):
        """상태 줄의 문구. 저장소 밖이면 비어 있다."""
        status = self.status
        if status is None or status.branch is None:
            return []
        text, color = describe(status)
        if not self._settled.is_set():
            text, color = "원격 확인 중", "ansibrightblack"  # 옛 숫자를 보여주면 push 가 안 된 것처럼 보인다
        parts = [("bold", status.branch), ("", "  "), (f"fg:{color}", text)]
        if status.to_add:
            parts += [("fg:ansibrightblack", "  ·  "), ("fg:ansiyellow", f"✎{status.to_add} add 필요")]
        if status.staged:
            parts += [("fg:ansibrightblack", "  ·  "), ("fg:ansicyan", f"●{status.staged} 커밋 대기")]
        if status.kind not in (NO_UPSTREAM, FAILED) and status.checked_at and self._settled.is_set():
            parts.append(("fg:ansibrightblack", f"  ·  원격 기준 {_age(self.clock() - status.checked_at)}"))
        return parts

    def check_once(self, network=True):
        """상태를 한 번 조사해 저장한다."""
        status = self._investigate(network)
        if status:
            to_add, staged = state.work_counts(status.cwd)
            status = replace(status, to_add=to_add, staged=staged)
        self._store(status)
        return status

    def _investigate(self, network):
        try:
            cwd = os.path.realpath(os.getcwd())
        except OSError:
            return None
        branch = state.current_branch(cwd)
        if state.repo_root(cwd) is None:
            return None
        if branch is None:
            return Status(cwd, "detached", NO_UPSTREAM)
        upstream = state.upstream(branch, cwd)
        if upstream is None:
            return Status(cwd, branch, NO_UPSTREAM)

        remote, name = upstream
        key = (cwd, remote, name)
        checked_at = self.status.checked_at if self.status and self.status.branch == branch else 0.0
        if network or key not in self._known:
            sha = state.remote_head(remote, name, cwd)
            if sha is None or (sha and not state.ensure_commit(remote, name, sha, cwd)):
                self.failures += 1
                return Status(cwd, branch, FAILED)
            self.failures = 0
            self._known[key] = sha
            checked_at = self.clock()
        sha = self._known[key]
        if not sha:
            return Status(cwd, branch, REMOTE_GONE, checked_at=checked_at)
        counts = state.ahead_behind(sha, cwd)
        if counts is None:
            return Status(cwd, branch, FAILED)
        ahead, behind = counts
        kind = DIVERGED if ahead and behind else BEHIND if behind else AHEAD if ahead else UP_TO_DATE
        return Status(cwd, branch, kind, ahead, behind, checked_at)

    def _next_interval(self):
        if not self.failures:
            return self.interval
        return self.retries[min(self.failures, len(self.retries)) - 1]

    def _store(self, status):
        changed = status != self.status
        self.status = status
        if changed and self.on_change:
            self.on_change()

    def _run(self):
        previous = None
        while not self._stopped:
            # 편집기처럼 CliGate 밖에서 고친 파일도 반영하도록 네트워크 없이 자주 다시 센다
            self._wake.wait(min(REDRAW_SECONDS, max(0.0, self._next_network - self.clock())))
            self._wake.clear()
            if self._paused or self._stopped:
                continue
            try:
                cwd = os.path.realpath(os.getcwd())
            except OSError:
                cwd = None
            branch = state.current_branch(cwd) if cwd else None
            # 폴더나 브랜치가 바뀌었거나 때가 되면 원격에 묻고, 그 밖에는 있는 정보로 다시 센다
            refresh = self._refresh_remote
            self._refresh_remote = False
            network = refresh or self.clock() >= self._next_network or (cwd, branch) != previous
            previous = (cwd, branch)
            self.check_once(network)
            if network:
                self._next_network = self.clock() + self._next_interval()
            if not self._refresh_remote:
                self._settled.set()
                if self.on_change:
                    self.on_change()
