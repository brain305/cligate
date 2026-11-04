import os
import select
import stat
import subprocess
import sys
import time

import pytest
from conftest import commit, git, judge, level, messages, write

from cligate import complete, statusline, state
from cligate.statusline import AHEAD, BEHIND, DIVERGED, FAILED, Monitor, NO_UPSTREAM, REMOTE_GONE, UP_TO_DATE


@pytest.fixture
def monitor(monkeypatch):
    m = Monitor()
    monkeypatch.setattr(statusline, "_current", m)
    return m


def text(monitor):
    return "".join(part for _, part in monitor.status_parts())


class TestKinds:
    def test_up_to_date(self, pushed, monitor):
        git("checkout", "-q", "main")
        assert monitor.check_once().kind == UP_TO_DATE

    def test_behind_without_fetching(self, pushed, monitor):
        status = monitor.check_once()
        assert (status.kind, status.behind, status.ahead) == (BEHIND, 2, 0)

    def test_ahead(self, pushed, monitor):
        git("checkout", "-q", "main")
        commit(pushed, "local")
        status = monitor.check_once()
        assert (status.kind, status.ahead, status.behind) == (AHEAD, 1, 0)

    def test_diverged(self, pushed, monitor):
        commit(pushed, "local")
        status = monitor.check_once()
        assert (status.kind, status.ahead, status.behind) == (DIVERGED, 1, 2)

    def test_no_upstream(self, repo, monitor):
        assert monitor.check_once().kind == NO_UPSTREAM

    def test_remote_branch_deleted(self, pushed, monitor):
        git("checkout", "-q", "mine")
        git("push", "-q", "origin", "--delete", "mine")
        git("config", "branch.mine.remote", "origin")
        git("config", "branch.mine.merge", "refs/heads/mine")
        assert monitor.check_once().kind == REMOTE_GONE

    def test_unreachable_remote(self, pushed, monitor):
        git("remote", "set-url", "origin", "/nosuch/remote.git")
        assert monitor.check_once().kind == FAILED

    def test_outside_repo(self, tmp_path, monkeypatch, monitor):
        monkeypatch.chdir(tmp_path)
        assert monitor.check_once() is None
        assert text(monitor) == ""

    def test_detached(self, pushed, monitor):
        git("checkout", "-q", "--detach")
        assert monitor.check_once().branch == "detached"

    def test_subfolder(self, pushed, monkeypatch, monitor):
        (pushed / "sub").mkdir()
        monkeypatch.chdir(pushed / "sub")
        assert monitor.check_once().kind == BEHIND


class TestSafety:
    def test_tracking_ref_and_fetch_head_untouched(self, pushed, monitor):
        before = git("rev-parse", "origin/feat")
        fetch_head = pushed / ".git" / "FETCH_HEAD"
        had = fetch_head.read_bytes() if fetch_head.exists() else None
        monitor.check_once()
        assert git("rev-parse", "origin/feat") == before
        assert (fetch_head.read_bytes() if fetch_head.exists() else None) == had

    def test_no_new_refs(self, pushed, monitor):
        refs = git("for-each-ref")
        monitor.check_once()
        assert git("for-each-ref") == refs

    def test_force_with_lease_still_protects(self, pushed, monitor):
        assert monitor.check_once().behind == 2
        commit(pushed, "mine2")
        with pytest.raises(subprocess.CalledProcessError) as caught:
            git("push", "--force-with-lease", "origin", "feat")
        assert "stale info" in caught.value.stderr

    def test_working_tree_untouched(self, pushed, monitor):
        write(pushed / "memo", "x\n")
        status = git("status", "--short")
        monitor.check_once()
        assert git("status", "--short") == status

    def test_hanging_remote_is_bounded(self, pushed, tmp_path, monkeypatch, monitor):
        script = tmp_path / "hang"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        git("remote", "set-url", "origin", "ssh://nobody@example.invalid/x.git")
        monkeypatch.setenv("GIT_SSH_COMMAND", str(script))
        monkeypatch.setattr(state, "NETWORK_TIMEOUT", 1)
        started = time.time()
        assert monitor.check_once().kind == FAILED
        assert time.time() - started < 5


class TestSchedule:
    def test_interval_backs_off_and_resets(self, pushed, monitor):
        assert monitor._next_interval() == 60
        url = git("remote", "get-url", "origin")
        git("remote", "set-url", "origin", "/nosuch/remote.git")
        monitor.check_once()
        assert monitor._next_interval() == 120
        monitor.check_once()
        assert monitor._next_interval() == 300
        monitor.check_once()
        assert monitor._next_interval() == 300
        git("remote", "set-url", "origin", url)
        monitor.check_once()
        assert monitor._next_interval() == 60

    def test_local_recount_uses_no_network(self, pushed, monitor, monkeypatch):
        monitor.check_once()
        git("fetch", "-q")
        git("merge", "-q", "--ff-only", "origin/feat")

        def fail(*args, **kwargs):
            pytest.fail("네트워크 호출")
        monkeypatch.setattr(state, "_git_network", fail)
        assert monitor.check_once(network=False).kind == UP_TO_DATE

    def test_notifies_only_on_change(self, pushed, monitor):
        calls = []
        monitor.on_change = lambda: calls.append(1)
        monitor.check_once()
        first = len(calls)
        monitor.check_once(network=False)
        assert len(calls) == first

    def test_thread_pauses_during_command(self, pushed):
        m = Monitor(interval=0.1)
        m.pause()
        m.start()
        time.sleep(0.5)
        assert m.status is None
        m.resume()
        for _ in range(100):
            if m.status:
                break
            time.sleep(0.05)
        m.stop()
        assert m.status.kind == BEHIND
        statusline._current = None


class TestDisplay:
    @pytest.mark.parametrize("kind, ahead, behind, expected", [
        (UP_TO_DATE, 0, 0, "✓ 최신"),
        (BEHIND, 0, 2, "↓2 pull 필요"),
        (AHEAD, 1, 0, "↑1 push 대기"),
        (DIVERGED, 1, 2, "↓2 ↑1 갈라짐"),
        (FAILED, 0, 0, "? 원격 확인 안 됨"),
        (NO_UPSTREAM, 0, 0, "원격 브랜치 없음"),
        (REMOTE_GONE, 0, 0, "원격에서 지워짐"),
    ])
    def test_report_wording(self, kind, ahead, behind, expected):
        assert statusline.describe(statusline.Status("/", "main", kind, ahead, behind))[0] == expected

    def test_one_line_with_branch_state_and_age(self, pushed, monitor):
        monitor.check_once()
        line = text(monitor)
        assert "\n" not in line
        assert "feat" in line
        assert "↓2 pull 필요" in line
        assert "원격 기준 0초 전" in line

    def test_age(self):
        now = [100.0]
        m = Monitor(clock=lambda: now[0])
        m.status = statusline.Status("/", "main", UP_TO_DATE, checked_at=92.0)
        assert "8초 전" in text(m)
        now[0] = 300.0
        assert "3분 전" in text(m)

    def test_no_age_when_failed(self, pushed, monitor):
        git("remote", "set-url", "origin", "/nosuch/remote.git")
        monitor.check_once()
        assert "원격 기준" not in text(monitor)


class TestSharedWithJudgementAndCompletion:
    def test_force_push_judgement_unchanged(self, pushed, monitor):
        monitor.check_once()
        assert level("git push --force") == 3

    def test_completion_uses_fresh_remote_state(self, pushed, monitor):
        assert [c.value for c in complete.candidates("git pu", str(pushed), curated=True)][0] == "push"
        monitor.check_once()
        assert [c.value for c in complete.candidates("git pu", str(pushed), curated=True)][0] == "pull"


class TestRefreshAfterRemoteCommands:
    @pytest.fixture
    def running(self, pushed):
        git("fetch", "-q")
        git("merge", "-q", "--ff-only", "origin/feat")
        commit(pushed, "mine2")
        m = Monitor(interval=3600)
        m.start()
        for _ in range(200):
            if m.status:
                break
            time.sleep(0.05)
        yield m
        m.stop()
        statusline._current = None

    def test_push_clears_ahead_immediately(self, running):
        assert running.status.kind == AHEAD
        running.pause()
        git("push", "-q")
        running.resume(refresh_remote=True)
        assert running.wait_settled(5)
        assert running.status.kind == UP_TO_DATE

    def test_without_refresh_old_value_stays(self, running):
        running.pause()
        git("push", "-q")
        running.resume()
        time.sleep(0.5)
        assert running.status.kind == AHEAD  # 평범한 명령 뒤에는 네트워크를 쓰지 않는다

    def test_pull_clears_behind_immediately(self, pushed):
        m = Monitor(interval=3600)
        m.start()
        for _ in range(200):
            if m.status:
                break
            time.sleep(0.05)
        assert m.status.kind == BEHIND
        m.pause()
        git("pull", "-q")
        m.resume(refresh_remote=True)
        assert m.wait_settled(5)
        kind = m.status.kind
        m.stop()
        statusline._current = None
        assert kind == UP_TO_DATE

    def test_never_shows_old_number_while_checking(self, running):
        running.pause()
        running._settled.clear()
        line = text(running)
        running._settled.set()
        assert "원격 확인 중" in line
        assert "push 대기" not in line
        assert "원격 기준" not in line

    def test_hanging_remote_does_not_hold_prompt(self, running, tmp_path, monkeypatch):
        script = tmp_path / "hang"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        git("remote", "set-url", "origin", "ssh://nobody@example.invalid/x.git")
        monkeypatch.setenv("GIT_SSH_COMMAND", str(script))
        monkeypatch.setattr(state, "NETWORK_TIMEOUT", 1)
        running.pause()
        running.resume(refresh_remote=True)
        started = time.time()
        running.wait_settled(0.2)
        assert time.time() - started < 1
        assert "원격 확인 중" in text(running)
        assert running.wait_settled(5)
        assert running.status.kind == FAILED


class TestTouchesRemote:
    @pytest.mark.parametrize("line, expected", [
        ("git push", True),
        ("git pull --rebase", True),
        ("git fetch", True),
        ("git add . && git push", True),
        ("sudo git push", True),
        ("git -C . pull", True),
        ("git status", False),
        ("git commit -m x", False),
        ("ls", False),
        ("echo git push", False),
    ])
    def test_detects(self, repo, line, expected):
        from cligate import gate, parse
        assert gate.touches_remote(parse.parse(line)) is expected

    def test_inside_alias(self, repo, monkeypatch):
        from cligate import gate, parse, shell
        monkeypatch.setattr(shell, "_loader", None)
        monkeypatch.setattr(shell, "_aliases", {"gp": "git push"})
        assert gate.touches_remote(parse.parse("gp")) is True


class TestWorkCounts:
    def test_clean(self, pushed, monitor):
        git("checkout", "-q", "main")
        status = monitor.check_once()
        assert (status.to_add, status.staged) == (0, 0)

    def test_modified_and_untracked_need_add(self, pushed, monitor):
        write(pushed / "init", "changed\n")
        write(pushed / "new", "x\n")
        status = monitor.check_once()
        assert (status.to_add, status.staged) == (2, 0)
        assert "✎2 add 필요" in text(monitor)

    def test_staged_waits_for_commit(self, pushed, monitor):
        write(pushed / "new", "x\n")
        git("add", "new")
        status = monitor.check_once()
        assert (status.to_add, status.staged) == (0, 1)
        assert "●1 커밋 대기" in text(monitor)

    def test_staged_then_modified_counts_both(self, pushed, monitor):
        write(pushed / "new", "x\n")
        git("add", "new")
        write(pushed / "new", "y\n")
        status = monitor.check_once()
        assert (status.to_add, status.staged) == (1, 1)

    def test_ignored_not_counted(self, repo, monitor):
        write(repo / "x.tmp", "x\n")
        assert monitor.check_once().to_add == 0

    def test_deleted_needs_add(self, pushed, monitor):
        (pushed / "init").unlink()
        assert monitor.check_once().to_add == 1

    def test_conflict_needs_add(self, repo, monitor):
        git("checkout", "-q", "-b", "side")
        write(repo / "a", "side\n")
        git("commit", "-qam", "side")
        git("checkout", "-q", "main")
        write(repo / "a", "main\n")
        git("commit", "-qam", "main")
        subprocess.run(["git", "merge", "side"], capture_output=True)
        assert monitor.check_once().to_add == 1

    def test_beside_pull_needed(self, pushed, monitor):
        write(pushed / "new", "x\n")
        monitor.check_once()
        line = text(monitor)
        assert line.index("pull 필요") < line.index("add 필요") < line.index("원격 기준")

    def test_shown_without_remote(self, repo, monitor):
        write(repo / "new", "x\n")
        monitor.check_once()
        assert "원격 브랜치 없음" in text(monitor)
        assert "✎1 add 필요" in text(monitor)

    def test_shown_when_remote_fails(self, pushed, monitor):
        git("remote", "set-url", "origin", "/nosuch/remote.git")
        write(pushed / "new", "x\n")
        monitor.check_once()
        assert "✎1 add 필요" in text(monitor)

    def test_local_recount_uses_no_network(self, pushed, monitor, monkeypatch):
        monitor.check_once()

        def fail(*args, **kwargs):
            pytest.fail("네트워크 호출")
        monkeypatch.setattr(state, "_git_network", fail)
        write(pushed / "new", "x\n")
        assert monitor.check_once(network=False).to_add == 1

    def test_counting_changes_nothing(self, pushed, monitor):
        write(pushed / "new", "x\n")
        before = git("status", "--porcelain")
        monitor.check_once()
        assert git("status", "--porcelain") == before

    def test_outside_changes_reach_screen(self, pushed, monkeypatch):
        monkeypatch.setattr(statusline, "REDRAW_SECONDS", 0.1)
        m = Monitor()
        m.start()
        for _ in range(100):
            if m.status:
                break
            time.sleep(0.05)
        write(pushed / "new", "x\n")
        for _ in range(100):
            if m.status.to_add:
                break
            time.sleep(0.05)
        m.stop()
        statusline._current = None
        assert m.status.to_add == 1


class TestCommitNotice:
    def test_notifies_when_behind(self, pushed, monitor):
        monitor.check_once()
        write(pushed / "new", "x\n")
        git("add", "new")
        assert level('git commit -m "x"') == 1
        assert "먼저 받으면 갈라지지 않습니다" in messages('git commit -m "x"')

    def test_advice_really_works_with_staged_changes(self, pushed, monitor):
        monitor.check_once()
        write(pushed / "new", "x\n")
        git("add", "new")
        git("pull", "-q", "--rebase", "--autostash")
        assert (pushed / "new").exists()
        assert git("rev-list", "--count", "HEAD..origin/feat") == "0"

    @pytest.mark.parametrize("line", ["git status", "git add .", "git commit --dry-run"])
    def test_quiet_otherwise(self, pushed, monitor, line):
        monitor.check_once()
        assert level(line) == 0

    def test_up_to_date_is_quiet(self, pushed, monitor):
        git("checkout", "-q", "main")
        monitor.check_once()
        assert level('git commit -m "x"') == 0

    def test_unknown_status_is_quiet(self, pushed, monitor):
        assert level('git commit -m "x"') == 0

    def test_not_blocked_and_recorded(self, pushed):
        write(pushed / "new", "x\n")
        git("add", "new", cwd=pushed)
        result = subprocess.run([sys.executable, "-c", "from cligate.cli import main; main()"],
                                input='true\ntrue\ngit commit -q -m ok\nexit\n', cwd=pushed,
                                capture_output=True, text=True, timeout=30)
        assert git("log", "-1", "--format=%s", cwd=pushed) == "ok"
        assert "(y/N)" not in result.stdout


class TestRealTerminal:
    def _spawn(self, cwd):
        import pty
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(cwd)
            os.environ.update(PROMPT_TOOLKIT_NO_CPR="1", TERM="xterm")
            os.execv(sys.executable, [sys.executable, "-c", "from cligate.cli import main; main()"])
        return pid, fd

    def _read(self, fd, seconds):
        out = b""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    break
        return out.decode("utf-8", "replace")

    def _quit(self, pid, fd):
        os.write(fd, b"\x15\x04")
        self._read(fd, 0.3)
        os.waitpid(pid, 0)
        os.close(fd)

    def test_shows_behind_on_screen(self, pushed):
        pid, fd = self._spawn(pushed)
        screen = self._read(fd, 2.5)
        self._quit(pid, fd)
        assert "feat" in screen
        assert "pull 필요" in screen
        assert "cligate" in screen

    def test_push_status_correct_right_after(self, pushed):
        git("fetch", "-q", cwd=pushed)
        git("merge", "-q", "--ff-only", "origin/feat", cwd=pushed)
        commit(pushed, "mine2")
        pid, fd = self._spawn(pushed)
        assert "push 대기" in self._read(fd, 2.5)
        os.write(fd, b"git push -q\r")
        screen = self._read(fd, 2.5)
        self._quit(pid, fd)
        assert "최신" in screen
        assert screen.rfind("push 대기") < screen.rfind("최신")

    def test_updates_after_pull(self, pushed):
        pid, fd = self._spawn(pushed)
        assert "pull 필요" in self._read(fd, 2.5)
        os.write(fd, b"git pull -q\r")
        screen = self._read(fd, 3)
        self._quit(pid, fd)
        assert "최신" in screen

    def test_updates_after_switching_branch(self, pushed):
        pid, fd = self._spawn(pushed)
        self._read(fd, 2.5)
        os.write(fd, b"git switch -q main\r")
        screen = self._read(fd, 3)
        self._quit(pid, fd)
        assert "main" in screen and "최신" in screen

    def test_typing_is_not_blocked_by_hanging_remote(self, pushed, tmp_path):
        script = tmp_path / "hang"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        git("remote", "set-url", "origin", "ssh://nobody@example.invalid/x.git")
        env_before = os.environ.get("GIT_SSH_COMMAND")
        os.environ["GIT_SSH_COMMAND"] = str(script)
        try:
            pid, fd = self._spawn(pushed)
            self._read(fd, 1.0)
            os.write(fd, b"touch made\r")
            self._read(fd, 1.5)
            assert (pushed / "made").exists()  # 원격이 멈춰 있어도 명령은 바로 돈다
            screen = self._read(fd, 5)
            self._quit(pid, fd)
            assert "원격 확인 안 됨" in screen
        finally:
            if env_before is None:
                del os.environ["GIT_SSH_COMMAND"]
            else:
                os.environ["GIT_SSH_COMMAND"] = env_before

    def test_outside_repo_has_no_status_line(self, tmp_path):
        pid, fd = self._spawn(tmp_path)
        screen = self._read(fd, 2)
        self._quit(pid, fd)
        assert "원격" not in screen
