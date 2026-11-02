"""판정 테스트의 공통 준비. 모든 테스트는 임시 폴더의 저장소에서 돈다.

gate.check 는 판정만 하고 실행하지 않으므로 rm -rf / 같은 줄도 안전하게 시험할 수 있다.
실제로 실행하는 시험은 test_cli.py 뿐이고, 그곳에는 임시 폴더 안의 대상만 쓴다.
"""
import os
import subprocess

import pytest

from cligate import gate, parse


def git(*args, cwd=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def write(path, text="v\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def commit(cwd, name):
    write(cwd / name, f"{name}\n")
    git("add", name, cwd=cwd)
    git("commit", "-qm", f"{name} 커밋", cwd=cwd)


def judge(line):
    return gate.check(parse.parse(line))


def level(line):
    return judge(line).level


def messages(line):
    return "\n".join(str(f) for f in judge(line).findings)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """사용자의 git 설정과 홈 폴더가 결과에 끼어들지 않게 한다."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Me")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "me@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Me")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "me@example.com")
    for name in ("GIT_SSH", "GIT_SSH_COMMAND", "BUILD", "NOPE", "X"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """커밋 두 개가 있고 변경은 없는 저장소."""
    path = tmp_path / "repo"
    path.mkdir()
    git("init", "-q", "-b", "main", cwd=path)
    for name in ("a", "b", "c", "d/x", "logs/1.log", "logs/2.log"):
        write(path / name)
    write(path / ".gitignore", "ig/\n*.tmp\n")
    git("add", "-A", cwd=path)
    git("commit", "-qm", "first", cwd=path)
    write(path / "b", "v2\n")
    git("add", "-A", cwd=path)
    git("commit", "-qm", "second", cwd=path)
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def pushed(tmp_path, monkeypatch):
    """원격과 내 저장소. feat 에는 팀원이 올린 커밋 2개가 있고 나는 아직 받지 않았다.

    merged 는 main 에 병합됨, mine 은 내 로컬 브랜치에도 있음, gone 은 로컬 브랜치를 지운 상태다.
    """
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    other = tmp_path / "other"
    git("init", "-q", "--bare", "-b", "main", str(origin))
    git("clone", "-q", str(origin), str(work))
    git("checkout", "-q", "-B", "main", cwd=work)
    commit(work, "init")
    git("push", "-q", "-u", "origin", "main", cwd=work)

    git("checkout", "-q", "-b", "merged", cwd=work)
    commit(work, "merged")
    git("push", "-q", "-u", "origin", "merged", cwd=work)
    git("checkout", "-q", "main", cwd=work)
    git("merge", "-q", "--ff-only", "merged", cwd=work)
    git("push", "-q", "origin", "main", cwd=work)

    git("checkout", "-q", "-b", "mine", cwd=work)
    commit(work, "mine")
    git("push", "-q", "-u", "origin", "mine", cwd=work)

    git("checkout", "-q", "-b", "gone", "main", cwd=work)
    commit(work, "gone")
    git("push", "-q", "-u", "origin", "gone", cwd=work)
    git("checkout", "-q", "main", cwd=work)
    git("branch", "-qD", "gone", cwd=work)

    git("checkout", "-q", "-b", "feat", "main", cwd=work)
    commit(work, "feat")
    git("push", "-q", "-u", "origin", "feat", cwd=work)

    git("clone", "-q", str(origin), str(other))
    git("checkout", "-q", "feat", cwd=other)
    commit(other, "team1")
    commit(other, "team2")
    git("push", "-q", "origin", "feat", cwd=other)

    monkeypatch.chdir(work)
    return work
