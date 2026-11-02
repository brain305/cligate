import stat
import time

import pytest
from conftest import commit, git, judge, write

from cligate import gate, state
from cligate.risk import L0, L2, L3


@pytest.fixture
def no_network(monkeypatch):
    """네트워크를 쓰는 함수에 닿는 순간 시험을 실패시킨다."""
    def fail(args, cwd):
        pytest.fail(f"네트워크 호출: {args}")
    monkeypatch.setattr(state, "_git_network", fail)


@pytest.mark.parametrize("line", [
    "git push --force",
    "git push -f origin feat",
    "git push -f origin HEAD",
    "git push -f origin main",
    "git push -f origin brandnew",
    "git push -f nosuchremote feat",
    "git push origin --delete merged",
    "git push origin --delete mine",
    "git push origin --delete gone",
    "git push origin --delete main",
    "git push origin --delete v1",
    "git push origin :refs/tags/v1",
    "git push --all -f",
    "git push",
])
def test_preview_never_touches_network(pushed, no_network, line):
    gate.preview(line)


def test_real_remote_is_not_contacted(pushed, tmp_path, monkeypatch):
    log = tmp_path / "ssh.log"
    script = tmp_path / "myssh"
    script.write_text(f"#!/bin/sh\necho called >> {log}\nexit 1\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    git("remote", "add", "far", "ssh://nobody@example.invalid/x.git")
    monkeypatch.setenv("GIT_SSH_COMMAND", str(script))

    gate.preview("git push -f far feat")
    assert not log.exists()
    judge("git push -f far feat")  # 실행 때의 판정은 원격에 묻는다
    assert log.exists()


def test_preview_returns_quickly_on_hanging_remote(pushed, tmp_path, monkeypatch):
    script = tmp_path / "hang"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    git("remote", "add", "slow", "ssh://nobody@example.invalid/x.git")
    monkeypatch.setenv("GIT_SSH_COMMAND", str(script))
    started = time.time()
    gate.preview("git push -f slow feat")
    assert time.time() - started < 2


def test_preview_does_not_change_anything(pushed):
    refs = git("for-each-ref")
    gate.preview("git push --force")
    gate.preview("git push origin --delete feat")
    assert git("for-each-ref") == refs


class TestStaleInfo:
    def test_unseen_team_commits_are_found_only_when_executing(self, pushed):
        # 추적 정보에는 팀원 커밋이 없어서 미리보기는 사라질 커밋을 보여줄 수 없다
        assert "사라집니다" not in "\n".join(str(f) for f in gate.preview("git push --force").findings)
        assert gate.preview("git push --force").level == L2
        assert judge("git push --force").level == L3

    def test_after_fetch_preview_sees_them(self, pushed):
        git("fetch", "-q")
        verdict = gate.preview("git push --force")
        assert verdict.level == L3
        assert verdict.word == "force"
        assert "team1" in str(verdict.findings[0])

    def test_estimate_is_labelled(self, pushed):
        assert "마지막 fetch 기준" in str(gate.preview("git push --force").findings[0])


@pytest.mark.parametrize("line, expected", [
    ("git push -f origin main", L3),                # 보호 브랜치는 상태와 관계없다
    ("git push origin --delete main", L3),
    ("git push --all -f", L3),
    ("git push origin --delete gone", L3),          # 추적 정보만 있는 커밋
    ("git push origin --delete mine", L2),
    ("git push origin --delete merged", L2),        # 온라인이면 L1, 원격 미확인이라 올린다
    ("git push -f origin brandnew", L2),            # 온라인이면 L0, 없는 브랜치인지 알 수 없다
    ("git push origin --delete nosuch", L2),
    ("git push", L0),
    ("git push --force-with-lease", L0),
    ("git status", L0),
])
def test_preview_levels(pushed, line, expected):
    assert gate.preview(line).level == expected


def test_never_says_safe_when_remote_is_unknown(pushed):
    git("fetch", "-q")
    git("reset", "-q", "--hard", "origin/feat")
    assert judge("git push -f").level < L2
    assert gate.preview("git push -f").level == L2


@pytest.mark.parametrize("line, expected", [
    ("rm new.txt", L2),
    ("git reset --hard", L2),
    ("ls", L0),
    ("rm -rf /", L3),
])
def test_local_judgement_is_same_as_executing(repo, line, expected):
    write(repo / "new.txt")
    write(repo / "a", "changed\n")
    assert gate.preview(line).level == judge(line).level == expected


@pytest.mark.parametrize("line", ['echo "hel', "rm 'a", "git push -f origin", "git push -", "git ", "rm ", "| ", "&&", ""])
def test_half_typed_lines_do_not_crash(pushed, line):
    gate.preview(line)


def test_preview_does_not_change_later_execution_judgement(pushed):
    gate.preview("git push --force")
    verdict = judge("git push --force")
    assert verdict.level == L3
    assert "마지막 fetch" not in str(verdict.findings[0])
    commit(pushed, "more")
    assert judge("git push --force").level == L3
