import os
import stat
import subprocess
import sys

import pytest
from conftest import git, write

from cligate import complete, learn, parse, risk


def use(line, times=1):
    for _ in range(times):
        learn.record(parse.parse(line))


def history():
    with open(learn.history_path(), encoding="utf-8") as f:
        return f.read()


def auto(text, cwd):
    return [c.value for c in complete.candidates(text, str(cwd), curated=True)]


class TestWhatIsStored:
    @pytest.mark.parametrize("line, entry", [
        ("ls -la secret-folder", "ls"),
        ("git status", "git status"),
        ('git commit -m "비밀 메시지"', "git commit -m"),
        ("git add .", "git add ."),
        ("git add secret.txt", "git add"),
        ("git push --force origin secret-branch", "git push --force"),
        ("git push --repo=https://user:token@host/x", "git push --repo"),
        ("git -c http.extraheader=token push", "git push"),
        ("git -C /secret/path status", "git status"),
        ("sudo git status", "git status"),
        ("TOKEN=abc123 git status", "git status"),
        ("/usr/bin/python3 secret.py", "python3"),
    ])
    def test_only_names(self, repo, line, entry):
        use(line)
        assert history().splitlines() == [entry]

    @pytest.mark.parametrize("line", ['git commit -mhello', "git commit --message=hello", "git reset abc123"])
    def test_values_never_stored(self, repo, line):
        use(line)
        assert "hello" not in history()
        assert "abc123" not in history()

    def test_every_command_on_the_line(self, repo):
        use("git add . && git status; ls")
        assert history().splitlines() == ["git add .", "git status", "ls"]

    def test_variable_command_not_stored(self, repo):
        use("$CMD a")
        assert not os.path.exists(learn.history_path())

    def test_file_is_private(self, repo):
        use("ls")
        assert stat.S_IMODE(os.stat(learn.history_path()).st_mode) == 0o600


class TestWindow:
    def test_trimmed_to_recent(self, repo):
        os.makedirs(os.path.dirname(learn.history_path()))
        with open(learn.history_path(), "w", encoding="utf-8") as f:
            f.write("oldcmd-long-enough-to-fill-the-file-up\n" * 1200)
        use("ls")
        lines = history().splitlines()
        assert len(lines) == learn.MAX_ENTRIES
        assert lines[-1] == "ls"

    def test_only_last_thousand_count(self, repo):
        os.makedirs(os.path.dirname(learn.history_path()))
        with open(learn.history_path(), "w", encoding="utf-8") as f:
            f.write("git merge\n" * 50 + "ls\n" * 1000)
        assert learn.rank(["status", "merge"], "git") == ["status", "merge"]

    def test_unwritable_location_does_not_crash(self, repo, tmp_path, monkeypatch):
        blocked = tmp_path / "file"
        blocked.write_text("")
        monkeypatch.setenv("XDG_STATE_HOME", str(blocked))
        use("ls")
        assert learn.rank(["a", "b"]) == ["a", "b"]


class TestOrder:
    def test_default_order_without_history(self, repo):
        assert learn.rank(["status", "add", "commit"], "git") == ["status", "add", "commit"]

    def test_one_use_does_not_flip_the_default(self, repo):
        use("git mergetool")
        names = auto("git ", repo)
        assert names.index("status") < names.index("mergetool")

    def test_frequent_command_rises(self, repo):
        use("git log", 30)
        assert auto("git ", repo)[0] == "log"

    def test_order_follows_use(self, repo):
        use("git diff", 40)
        use("git log", 20)
        names = auto("git ", repo)
        assert names.index("diff") < names.index("log") < names.index("branch")

    def test_order_changes_again_with_new_habits(self, repo):
        use("git log", 20)
        assert auto("git ", repo)[0] == "log"
        use("git diff", 40)
        assert auto("git ", repo)[0] == "diff"

    def test_option_order_learned_per_command(self, repo):
        use("git add -p", 30)
        assert auto("git add ", repo)[0] == "-p"
        use("git push --tags", 30)
        assert auto("git push --", repo)[0] == "--tags"
        assert auto("git commit ", repo)[0] == "-m"

    def test_shell_command_order(self, repo):
        use("mkdir x", 30)
        assert auto("", repo)[0] == "mkdir"

    def test_unknown_command_appears_after_enough_use(self, repo):
        use("npm test", 2)
        assert "npm" not in auto("", repo)
        use("npm test", 1)
        assert "npm" in auto("", repo)
        assert auto("np", repo)[0] == "npm"

    def test_unknown_git_subcommand_appears_after_enough_use(self, repo):
        assert "bisect" not in auto("git ", repo)
        use("git bisect", 3)
        assert "bisect" in auto("git ", repo)

    def test_unknown_git_option_appears_after_enough_use(self, repo):
        assert "--atomic" not in auto("git push --", repo)
        use("git push --atomic", 3)
        assert "--atomic" in auto("git push --", repo)


class TestSafetyStaysOnTop:
    def test_risky_option_never_rises(self, pushed):
        use("git push --force", 100)
        git("checkout", "-q", "main")
        names = auto("git push --", pushed)
        assert names[-1] == "--force"

    def test_risky_first_argument_never_rises(self, repo):
        use("git reset --hard", 100)
        write(repo / "a", "changed\n")
        found = complete.candidates("git reset ", str(repo), curated=True)
        assert found[-1].value == "--hard"
        assert found[-1].level == risk.L2

    def test_state_rules_beat_habits(self, repo):
        use("git log", 100)
        write(repo / "a", "changed\n")
        assert auto("git ", repo)[:2] == ["add", "stash"]

    def test_learning_does_not_change_judgement(self, repo):
        from cligate import gate
        use("git reset --hard", 100)
        write(repo / "a", "changed\n")
        assert gate.preview("git reset --hard").level == 2


def run(lines, cwd):
    return subprocess.run([sys.executable, "-c", "from cligate.cli import main; main()"],
                          input="\n".join([*lines, ""]), cwd=cwd, capture_output=True, text=True, timeout=30)


class TestRealFlow:
    def test_executed_commands_recorded(self, repo):
        run(["git status", "cd d", "exit"], repo)
        assert history().splitlines() == ["git status", "cd"]

    def test_cancelled_not_recorded(self, repo):
        write(repo / "new.txt")
        run(["rm new.txt", "", "exit"], repo)
        assert not os.path.exists(learn.history_path())

    def test_confirmed_recorded(self, repo):
        write(repo / "new.txt")
        run(["rm new.txt", "y", "exit"], repo)
        assert history().splitlines() == ["rm"]

    def test_typo_not_recorded(self, repo):
        run(["gti status", "exit"], repo)
        assert not os.path.exists(learn.history_path())

    def test_syntax_error_not_recorded(self, repo):
        run(['echo "hi', "exit"], repo)
        assert not os.path.exists(learn.history_path())

    def test_exit_not_recorded(self, repo):
        run(["exit"], repo)
        assert not os.path.exists(learn.history_path())
