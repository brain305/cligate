import os

import pytest
from conftest import commit, git, judge, level, messages, write

from cligate import state
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


class TestAddDotInSubfolder:
    @pytest.fixture
    def nested(self, repo, monkeypatch):
        write(repo / "notes.txt", "x\n")
        write(repo / "d" / "x", "changed\n")
        monkeypatch.chdir(repo / "d")
        return repo

    def test_notifies_what_stays_outside(self, nested):
        assert level("git add .") == L1
        text = messages("git add .")
        assert "지금 폴더(d) 아래만" in text
        assert "바깥에 1개가" in text
        assert "../notes.txt" in text
        assert "git add -A" in text

    def test_inside_files_are_not_listed(self, nested):
        assert "    x" not in messages("git add .")

    def test_does_not_block(self, nested):
        assert judge("git add .").word is None

    @pytest.mark.parametrize("line", ["git add -A", "git add --all", "git add x", "git add -n .", "git status"])
    def test_other_forms_are_quiet(self, nested, line):
        assert level(line) == L0

    def test_at_repo_root_is_quiet(self, repo):
        write(repo / "notes.txt", "x\n")
        assert level("git add .") == L0

    def test_nothing_outside_is_quiet(self, repo, monkeypatch):
        write(repo / "d" / "x", "changed\n")
        monkeypatch.chdir(repo / "d")
        assert level("git add .") == L0

    def test_staged_outside_does_not_count(self, repo, monkeypatch):
        write(repo / "notes.txt", "x\n")
        git("add", "notes.txt")
        monkeypatch.chdir(repo / "d")
        assert level("git add .") == L0

    def test_modified_outside_counts(self, repo, monkeypatch):
        write(repo / "a", "changed\n")
        monkeypatch.chdir(repo / "d")
        assert "../a" in messages("git add .")

    def test_after_cd_in_same_line(self, repo):
        write(repo / "notes.txt", "x\n")
        assert level("cd d && git add .") == L1

    def test_many_are_cut(self, repo, monkeypatch):
        for i in range(20):
            write(repo / f"f{i}", "x\n")
        monkeypatch.chdir(repo / "d")
        assert "외 12개" in messages("git add .")

    def test_judging_adds_nothing(self, nested):
        before = git("status", "--porcelain")
        judge("git add .")
        assert git("status", "--porcelain") == before


class TestSwitchForce:
    @pytest.mark.parametrize("line", ["git switch -f side", "git switch --force side", "git switch --discard-changes side", "git switch -fc new"])
    def test_loses_changes(self, repo, line):
        git("branch", "side")
        write(repo / "a", "changed\n")
        assert level(line) == L2
        assert "a  (커밋 안 한 변경)" in messages(line)

    def test_clean_repo_passes(self, repo):
        git("branch", "side")
        assert level("git switch -f side") == L0

    def test_plain_switch_passes(self, repo):
        git("branch", "side")
        write(repo / "a", "changed\n")
        assert level("git switch side") == L0
        assert level("git switch -c new") == L0

    def test_many_changes_need_word(self, repo):
        for i in range(60):
            write(repo / f"f{i}")
        git("add", "-A")
        git("commit", "-qm", "many")
        for i in range(60):
            write(repo / f"f{i}", "changed\n")
        assert level("git switch -f main") == L3


class TestBranchDelete:
    @pytest.fixture
    def branches(self, repo):
        """merged 는 병합됨 / alone 은 커밋이 여기에만 있음 / main 이 지금 브랜치."""
        git("branch", "merged")
        git("checkout", "-q", "-b", "alone")
        commit(repo, "alone-file")
        git("checkout", "-q", "main")
        return repo

    @pytest.mark.parametrize("line", ["git branch -D alone", "git branch --delete --force alone", "git branch -d -f alone", "git branch -df alone"])
    def test_only_copy_of_commits_asks(self, branches, line):
        assert level(line) == L2
        text = messages(line)
        assert "어디에도 남지 않습니다" in text
        assert "alone-file" in text
        assert "git branch alone " in text

    def test_recovery_tip_really_works(self, branches):
        tip = next(l for l in messages("git branch -D alone").splitlines() if "되찾을" in l)
        sha = tip.split("git branch alone ")[1].split()[0]
        git("branch", "-D", "alone")
        git("branch", "alone", sha)
        assert git("log", "-1", "--format=%s", "alone") == "alone-file 커밋"

    def test_merged_passes(self, branches):
        assert level("git branch -D merged") == L0

    def test_commits_on_another_local_branch_pass(self, branches):
        git("branch", "copy", "alone")
        assert level("git branch -D alone") == L0

    @pytest.mark.parametrize("line", ["git branch -d alone", "git branch alone2", "git branch", "git branch -a", "git branch -rD origin/alone"])
    def test_git_protects_or_nothing_lost(self, branches, line):
        assert level(line) == L0

    @pytest.mark.parametrize("line", ["git branch -D main", "git branch -D nosuch"])
    def test_git_refuses_by_itself(self, branches, line):
        assert level(line) == L0

    def test_variable_asks(self, branches):
        assert level("git branch -D $B") == L2

    def test_several_take_the_highest(self, branches):
        assert level("git branch -D merged alone") == L2

    def test_stash_does_not_count_as_kept(self, branches):
        git("checkout", "-q", "alone")
        write(branches / "alone-file", "changed\n")
        git("stash", "-q")
        git("checkout", "-q", "main")
        assert level("git branch -D alone") == L2

    def test_remote_tracking_only_notifies(self, pushed):
        git("checkout", "-q", "main")
        assert level("git branch -D mine") == L1
        assert "원격 추적 브랜치에만" in messages("git branch -D mine")

    def test_unpushed_commit_on_tracked_branch_asks(self, pushed):
        git("checkout", "-q", "mine")
        commit(pushed, "unpushed")
        git("checkout", "-q", "main")
        assert level("git branch -D mine") == L2
        assert "unpushed" in messages("git branch -D mine")

    def test_judging_deletes_nothing(self, branches):
        judge("git branch -D alone")
        assert git("rev-parse", "--verify", "-q", "refs/heads/alone")


class TestStashDrop:
    @pytest.fixture
    def stashed(self, repo):
        write(repo / "a", "first\n")
        git("stash", "push", "-q", "-m", "첫째")
        write(repo / "b", "second\n")
        git("stash", "push", "-q", "-m", "둘째")
        return repo

    @pytest.mark.parametrize("line", ["git stash drop", "git stash drop stash@{0}", "git stash drop stash@{1}", "git stash drop 1", "git stash drop -q", "git stash clear"])
    def test_asks(self, stashed, line):
        assert level(line) == L2

    def test_shows_the_right_entry(self, stashed):
        assert "둘째" in messages("git stash drop")
        assert "첫째" not in messages("git stash drop")
        assert "첫째" in messages("git stash drop stash@{1}")
        assert "둘째" not in messages("git stash drop 1")

    def test_clear_lists_all(self, stashed):
        assert "첫째" in messages("git stash clear")
        assert "둘째" in messages("git stash clear")

    @pytest.mark.parametrize("line", ["git stash drop", "git stash clear"])
    def test_nothing_stashed_passes(self, repo, line):
        assert level(line) == L0

    def test_missing_entry_passes(self, stashed):
        assert level("git stash drop stash@{9}") == L0

    @pytest.mark.parametrize("line", ["git stash", "git stash list", "git stash pop", "git stash push -m x", "git stash show"])
    def test_other_stash_commands_pass(self, stashed, line):
        assert level(line) == L0

    def test_variable_asks(self, stashed):
        assert level("git stash drop $S") == L2

    def test_large_stash_needs_word(self, repo):
        for i in range(60):
            write(repo / f"f{i}")
        git("add", "-A")
        git("commit", "-qm", "many")
        for i in range(60):
            write(repo / f"f{i}", "changed\n")
        git("stash", "-q")
        verdict = judge("git stash drop")
        assert verdict.level == L3
        assert verdict.word == "delete"

    def test_judging_deletes_nothing(self, stashed):
        judge("git stash clear")
        assert len(state.stash_entries(str(stashed))) == 2


def test_outside_repo_git_refuses_by_itself(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for line in ("git reset --hard", "git clean -fd", "git checkout .", "git restore .", "git switch -f x", "git branch -D x", "git stash clear"):
        assert level(line) == L0
