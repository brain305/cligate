import os

import pytest
from conftest import git, judge, level, messages, write

from cligate.risk import L0, L1, L2, L3


@pytest.mark.parametrize("line", [
    "git reset --hard",
    "git checkout .",
    "git restore .",
    "git clean -fd",
    "git checkout -b new",
    "git checkout main",
    "git checkout -",
    "git status",
])
def test_nothing_to_lose_passes(repo, line):
    assert level(line) == L0


def test_reset_to_older_commit_only_notifies(repo):
    assert level("git reset --hard HEAD~1") == L1
    assert "git reflog" in messages("git reset --hard HEAD~1")


@pytest.mark.parametrize("line", ["git checkout $B"])
def test_checkout_of_unknown_name_notifies(repo, line):
    assert level(line) == L1


@pytest.fixture
def dirty(repo):
    """a, d/x 는 스테이지 안 됨 / st, c 는 스테이지됨 / b 는 삭제 / u, nd 는 미추적 / ig 는 무시됨."""
    write(repo / "a", "changed\n")
    write(repo / "d" / "x", "changed\n")
    write(repo / "st")
    write(repo / "c", "changed\n")
    git("add", "st", "c")
    os.remove(repo / "b")
    write(repo / "u")
    write(repo / "nd" / "z")
    write(repo / "ig" / "k")
    return repo


@pytest.mark.parametrize("line, expected", [
    ("git reset --hard", L2),
    ("git reset --hard HEAD~1", L2),
    ("git reset --hard $X", L2),
    ("git reset", L0),
    ("git reset --soft HEAD~1", L0),
    ("git reset --hard && ls", L2),

    ("git checkout .", L2),
    ("git checkout -- a", L2),
    ("git checkout a", L2),
    ("git checkout -- c", L0),       # 스테이지된 내용과 같아서 잃을 것이 없다
    ("git checkout -- b", L0),       # 지운 파일이 되살아날 뿐이다
    ("git checkout HEAD -- c", L2),  # 커밋을 지정하면 스테이지된 내용까지 덮어쓴다
    ("git checkout HEAD c", L2),
    ("git checkout main", L0),
    ("git checkout -f main", L2),
    ("git checkout -p", L0),
    ("git checkout -b nb", L0),
    ("git checkout -- $X", L2),
    ("git checkout --pathspec-from-file=f", L2),
    ("cd d && git checkout .", L2),
    ("git -C d checkout x", L2),

    ("git restore .", L2),
    ("git restore a", L2),
    ("git restore c", L0),
    ("git restore --staged c", L0),
    ("git restore --staged --worktree c", L2),
    ("git restore -SW c", L2),
    ("git restore --source=HEAD a", L2),
    ("git restore --source HEAD a", L2),
    ("git restore -p a", L0),
    ("git restore", L0),
    ("git restore $X", L2),

    ("git clean -fd", L2),
    ("git clean -f -d", L2),
    ("git clean -fdx", L2),
    ("git clean -f", L2),
    ("git clean -fd nd", L2),
    ("git clean -nd", L0),
    ("git clean -d", L0),
    ("git clean -fi", L0),
    ("git clean -fd nosuch", L0),
    ("git clean -f $X", L2),
    ("git clean -fdX", L2),
    ("git clean -fX", L0),           # -d 가 없으면 무시된 폴더 ig 를 지우지 않는다
])
def test_dirty_repo(dirty, line, expected):
    assert level(line) == expected


def test_reset_lists_what_is_lost(dirty):
    text = messages("git reset --hard")
    for name in ("a", "c", "d/x", "st"):
        assert f"{name}  (커밋 안 한 변경)" in text
    assert "규모: 파일 4개" in text
    assert "    b  (" not in text
    assert "    u  (" not in text


def test_clean_x_includes_ignored_only_with_x(dirty):
    assert "ig" not in messages("git clean -fd")
    assert "ig" in messages("git clean -fdx")


def test_clean_x_without_d_really_keeps_ignored_dir(dirty):
    git("clean", "-fX")
    assert (dirty / "ig" / "k").exists()


def test_clean_does_not_delete_when_judging(dirty):
    judge("git clean -fdx")
    assert (dirty / "u").exists()
    assert (dirty / "ig" / "k").exists()


def test_clean_only_empty_dir_passes(repo):
    (repo / "empty").mkdir()
    assert level("git clean -fd") == L0


def test_clean_without_force_when_not_required(dirty):
    git("config", "clean.requireForce", "false")
    assert level("git clean -d") == L2


def test_many_changes_need_word(repo):
    for i in range(60):
        write(repo / f"f{i}.txt")
    git("add", "-A")
    git("commit", "-qm", "many")
    for i in range(60):
        write(repo / f"f{i}.txt", "changed\n")
    for line in ("git reset --hard", "git checkout .", "git restore ."):
        verdict = judge(line)
        assert verdict.level == L3
        assert verdict.word == "delete"


def test_many_untracked_files_need_word_for_clean(repo):
    for i in range(60):
        write(repo / f"f{i}.txt")
    assert level("git clean -f") == L3


def test_outside_repo_git_refuses_by_itself(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for line in ("git reset --hard", "git clean -fd", "git checkout .", "git restore ."):
        assert level(line) == L0
