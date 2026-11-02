"""실제 cligate 를 띄워 입력을 넣고 결과를 본다. 대상은 모두 임시 폴더 안에 있다."""
import subprocess
import sys

import pytest
from conftest import git, write


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
