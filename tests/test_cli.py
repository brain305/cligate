"""실제 cligate 를 띄워 입력을 넣고 결과를 본다. 대상은 모두 임시 폴더 안에 있다."""
import subprocess
import sys

import pytest
from conftest import commit, git, write

from cligate import cli


def run(lines, cwd):
    return subprocess.run(
        [sys.executable, "-c", "from cligate.cli import main; main()"],
        input="\n".join([*lines, ""]), cwd=cwd, capture_output=True, text=True, timeout=30,
    )


class TestConfirm:
    def test_l1_runs_without_asking(self, repo):
        run(["rm a", "exit"], repo)
        assert not (repo / "a").exists()

    @pytest.mark.parametrize("answer", ["", "n"])
    def test_l2_cancelled(self, repo, answer):
        write(repo / "new.txt")
        run(["rm new.txt", answer, "exit"], repo)
        assert (repo / "new.txt").exists()

    def test_l2_confirmed(self, repo):
        write(repo / "new.txt")
        run(["rm new.txt", "y", "exit"], repo)
        assert not (repo / "new.txt").exists()

    @pytest.mark.parametrize("answer", ["y", "DELETE", ""])
    def test_l3_needs_exact_word(self, repo, answer):
        result = run(["rm -rf .git", answer, "exit"], repo)
        assert (repo / ".git").exists()
        assert "취소했습니다" in result.stdout

    def test_l3_word(self, repo):
        run(["rm -rf .git", "delete", "exit"], repo)
        assert not (repo / ".git").exists()

    def test_level_is_printed(self, repo):
        assert "[L1]" in run(["rm a", "exit"], repo).stdout


class TestGitWork:
    def test_reset_cancelled_keeps_change(self, repo):
        write(repo / "a", "changed\n")
        run(["git reset --hard", "", "exit"], repo)
        assert (repo / "a").read_text() == "changed\n"

    def test_reset_confirmed(self, repo):
        write(repo / "a", "changed\n")
        run(["git reset --hard", "y", "exit"], repo)
        assert (repo / "a").read_text() == "v\n"

    def test_clean_cancelled_then_confirmed(self, repo):
        write(repo / "u")
        run(["git clean -f", "", "exit"], repo)
        assert (repo / "u").exists()
        run(["git clean -f", "y", "exit"], repo)
        assert not (repo / "u").exists()


class TestPush:
    def test_force_needs_word(self, pushed):
        before = git("ls-remote", "origin", "refs/heads/feat", cwd=pushed)
        run(["git push -f", "y", "exit"], pushed)
        assert git("ls-remote", "origin", "refs/heads/feat", cwd=pushed) == before
        run(["git push -f", "force", "exit"], pushed)
        assert git("ls-remote", "origin", "refs/heads/feat", cwd=pushed) != before

    def test_delete_follows_conditions(self, pushed):
        run(["git push origin --delete merged", "exit"], pushed)
        assert git("ls-remote", "--heads", "origin", "merged", cwd=pushed) == ""
        run(["git push origin --delete gone", "y", "exit"], pushed)
        assert git("ls-remote", "--heads", "origin", "gone", cwd=pushed) != ""


class TestAnswerStaysOnScreen:
    def test_word_shown(self, repo):
        result = run(["rm -rf .git", "delete", "exit"], repo)
        assert "입력: delete" in result.stdout

    def test_wrong_word_shown(self, repo):
        result = run(["rm -rf .git", "y", "exit"], repo)
        assert "입력: y" in result.stdout
        assert "취소했습니다" in result.stdout

    def test_yes_shown(self, repo):
        write(repo / "new.txt")
        assert "입력: y" in run(["rm new.txt", "y", "exit"], repo).stdout

    def test_plain_enter_shows_only_cancel(self, repo):
        write(repo / "new.txt")
        result = run(["rm new.txt", "", "exit"], repo)
        assert "입력:" not in result.stdout
        assert "취소했습니다" in result.stdout


class TestUnpulledPush:
    def test_cancel_does_not_push(self, pushed):
        commit(pushed, "mine2")
        result = run(["git push", "", "exit"], pushed)
        assert "받지 않은 커밋 2개" in result.stdout
        assert "취소했습니다" in result.stdout
        assert "rejected" not in result.stdout + result.stderr

    def test_after_pull_pushes_without_asking(self, pushed):
        git("pull", "-q", "--no-rebase", cwd=pushed)
        commit(pushed, "mine2")
        before = git("ls-remote", "origin", "refs/heads/feat", cwd=pushed)
        run(["git push", "exit"], pushed)
        assert git("ls-remote", "origin", "refs/heads/feat", cwd=pushed) != before


class TestShell:
    def test_compound_cd_persists(self, repo):
        assert str((repo / "d").resolve()) in run(["cd d && cd .", "pwd", "exit"], repo).stdout

    def test_subshell_cd_does_not_persist(self, repo):
        lines = run(["(cd d)", "pwd", "exit"], repo).stdout
        assert str((repo / "d").resolve()) not in lines

    def test_subshell_exit_does_not_quit(self, repo):
        assert "still-here" in run(["(exit 3)", "echo still-here", "exit"], repo).stdout

    def test_exit_code(self, repo):
        assert run(["exit 5"], repo).returncode == 5

    def test_syntax_error_does_not_run(self, repo):
        run(['touch made; echo "hello', "exit"], repo)
        assert not (repo / "made").exists()


def test_windows_stops_with_guidance(monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "win32")
    with pytest.raises(SystemExit) as stopped:
        cli.main()
    assert stopped.value.code == 1
    assert "WSL" in capsys.readouterr().out


class TestOverwrite:
    @pytest.mark.parametrize("line", ["echo new > u", "cp a u", "mv a u"])
    def test_cancelled_keeps_file(self, repo, line):
        write(repo / "u", "precious\n")
        run([line, "", "exit"], repo)
        assert (repo / "u").read_text() == "precious\n"
        assert (repo / "a").exists()

    def test_redirect_confirmed(self, repo):
        write(repo / "u", "precious\n")
        run(["echo new > u", "y", "exit"], repo)
        assert (repo / "u").read_text() == "new\n"

    def test_cp_confirmed(self, repo):
        write(repo / "u", "precious\n")
        run(["cp a u", "y", "exit"], repo)
        assert (repo / "u").read_text() == "v\n"

    def test_append_does_not_ask(self, repo):
        write(repo / "u", "precious\n")
        run(["echo new >> u", "exit"], repo)
        assert (repo / "u").read_text() == "precious\nnew\n"

    def test_new_file_does_not_ask(self, repo):
        run(["echo new > brand-new", "cp a brand-new2", "exit"], repo)
        assert (repo / "brand-new").read_text() == "new\n"
        assert (repo / "brand-new2").exists()

    def test_committed_file_really_recovers(self, repo):
        run(["echo broken > a", "git restore a", "y", "exit"], repo)  # 덮어쓴 내용도 커밋 안 한 변경이라 묻는다
        assert (repo / "a").read_text() == "v\n"


class TestGitCleanupCommands:
    def _alone(self, repo):
        git("checkout", "-q", "-b", "alone", cwd=repo)
        commit(repo, "alone-file")
        git("checkout", "-q", "main", cwd=repo)

    def test_branch_delete_cancelled_then_confirmed(self, repo):
        self._alone(repo)
        run(["git branch -D alone", "", "exit"], repo)
        assert "alone" in git("branch", cwd=repo)
        run(["git branch -D alone", "y", "exit"], repo)
        assert "alone" not in git("branch", cwd=repo)

    def test_stash_drop_cancelled_then_confirmed(self, repo):
        write(repo / "a", "changed\n")
        git("stash", "-q", cwd=repo)
        run(["git stash drop", "", "exit"], repo)
        assert git("stash", "list", cwd=repo) != ""
        run(["git stash drop", "y", "exit"], repo)
        assert git("stash", "list", cwd=repo) == ""

    def test_switch_force_cancelled_then_confirmed(self, repo):
        git("branch", "side", cwd=repo)
        write(repo / "a", "changed\n")
        run(["git switch -f side", "", "exit"], repo)
        assert git("branch", "--show-current", cwd=repo) == "main"
        assert (repo / "a").read_text() == "changed\n"
        run(["git switch -f side", "y", "exit"], repo)
        assert git("branch", "--show-current", cwd=repo) == "side"
        assert (repo / "a").read_text() == "v\n"
