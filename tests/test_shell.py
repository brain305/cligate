import os
import shutil
import subprocess
import sys
import time

import pytest
from conftest import git, judge, level, messages, write

from cligate import complete, gate, parse, runner, shell
from cligate.risk import L0, L1, L2, L3

SHELLS = [s for s in ("/bin/zsh", "/bin/bash", "/bin/sh", "/bin/dash", "/bin/ksh") if os.path.exists(s)]
needs_zsh = pytest.mark.skipif(not os.path.exists("/bin/zsh"), reason="zsh 필요")


@pytest.fixture
def no_loader(monkeypatch):
    monkeypatch.setattr(shell, "_loader", None)
    monkeypatch.setattr(shell, "_definitions", [])
    monkeypatch.setattr(shell, "_aliases", {})


class TestChoosingShell:
    def test_uses_users_shell(self, monkeypatch):
        monkeypatch.setenv("SHELL", SHELLS[0])
        assert shell.path() == SHELLS[0]

    @pytest.mark.parametrize("value", ["/usr/bin/fish", "/bin/nosuchshell", "/nosuch/zsh", ""])
    def test_falls_back(self, monkeypatch, value):
        monkeypatch.setenv("SHELL", value)
        assert shell.path() == "/bin/sh"

    def test_unset_falls_back(self, monkeypatch):
        monkeypatch.delenv("SHELL")
        assert shell.path() == "/bin/sh"


@pytest.mark.parametrize("name", SHELLS)
class TestRunnerInEveryShell:
    @pytest.fixture(autouse=True)
    def use_shell(self, name, monkeypatch, no_loader):
        monkeypatch.setenv("SHELL", name)

    def test_exit_code(self, repo, name):
        assert runner.run("exit 7") == 7
        assert runner.run("true") == 0
        assert runner.run("false") == 1

    def test_cd_persists(self, repo, name):
        runner.run("cd d && pwd > /dev/null")
        assert os.getcwd() == os.path.realpath(repo / "d")

    def test_subshell_cd_does_not_persist(self, repo, name):
        runner.run("(cd d)")
        assert os.getcwd() == os.path.realpath(repo)

    def test_exit_after_cd_still_follows(self, repo, name):
        assert runner.run("cd d; exit 3") == 3
        assert os.getcwd() == os.path.realpath(repo / "d")

    def test_quotes_and_special_characters_survive(self, repo, name):
        runner.run("""printf '%s' "a'b \\$HOME" > out""")
        assert (repo / "out").read_text() == "a'b $HOME"

    def test_comment_and_pipe(self, repo, name):
        runner.run("echo hi | tr h j > out # 주석")
        assert (repo / "out").read_text() == "ji\n"

    def test_no_marker_left_behind(self, repo, name, tmp_path, monkeypatch):
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        import tempfile
        monkeypatch.setattr(tempfile, "tempdir", None)
        runner.run("true")
        assert not [f for f in os.listdir(tmp_path) if f.startswith("cligate-")]


@needs_zsh
class TestZshOnlyFeatures:
    @pytest.fixture(autouse=True)
    def use_zsh(self, monkeypatch, no_loader):
        monkeypatch.setenv("SHELL", "/bin/zsh")

    def test_process_substitution_passes_syntax_check(self, repo):
        assert parse.syntax_error("cat <(ls)") is None
        runner.run("cat <(echo 프로세스) > out")
        assert (repo / "out").read_text() == "프로세스\n"

    def test_real_syntax_error_still_stops(self, repo):
        assert parse.syntax_error('echo "hello')
        assert parse.syntax_error("if true; then")

    def test_recursive_glob_runs_in_zsh(self, repo):
        write(repo / "d" / "e" / "deep.py")
        runner.run("echo **/*.py > out")
        assert "d/e/deep.py" in (repo / "out").read_text()


def test_recursive_glob_is_judged(repo):
    """zsh 의 ** 는 아래 폴더까지 지우므로 판정도 그만큼 본다."""
    write(repo / "d" / "e" / "deep.py")
    assert level("rm **/*.py") == L2
    assert "deep.py" in messages("rm **/*.py")


def test_many_files_stay_fast(repo):
    for i in range(400):
        write(repo / f"f{i}.txt")
    started = time.time()
    verdict = judge("rm f*.txt")
    assert verdict.level == L3
    assert time.time() - started < 5
    assert "앞 50개만" in messages("rm f*.txt")


def test_many_committed_files_ask_with_note(repo):
    for i in range(80):
        write(repo / f"f{i}.txt")
    git("add", "-A")
    git("commit", "-qm", "many")
    assert level("rm f*.txt") == L2
    assert "앞 50개만" in messages("rm f*.txt")


@pytest.mark.parametrize("name", SHELLS)
def test_missing_command_message_is_plain(repo, name, monkeypatch, no_loader, capfd):
    monkeypatch.setenv("SHELL", name)
    assert runner.run("nosuchcommand-xyz") == 127
    err = capfd.readouterr().err
    assert "nosuchcommand-xyz" in err
    assert "eval" not in err


class TestEvalOnlyForAliases:
    @pytest.fixture(autouse=True)
    def aliases(self, monkeypatch, no_loader):
        monkeypatch.setattr(shell, "_aliases", {"gs": "git status"})
        monkeypatch.setattr(shell, "_definitions", ["alias gs='git status'"])

    @pytest.mark.parametrize("line", ["gs", "gs -s", "ls && gs", "ls; gs", "ls | gs", "(gs)"])
    def test_uses_eval(self, line):
        assert "eval" in shell.script(line, "true")
        assert "alias gs=" in shell.script(line, "true")

    @pytest.mark.parametrize("line", ["ls", "echo gsx", "git status"])
    def test_plain(self, line):
        script = shell.script(line, "true")
        assert "eval" not in script
        assert "alias" not in script
        assert script.endswith(line)


class TestParsingAliases:
    def test_zsh_format(self):
        output = "alias gs='git status'\nalias ll='ls -l'\nalias -g G='| grep'\nalias -s py=vim\nalias -- -='cd -'\n"
        definitions, found = shell._parse_aliases(output)
        assert found == {"gs": "git status", "ll": "ls -l", "-": "cd -"}
        assert "alias -g G='| grep'" not in definitions

    def test_noise_from_rc_ignored(self):
        _, found = shell._parse_aliases("환영합니다\nconda 초기화\nalias a=b\nalias broken='x\n")
        assert found == {"a": "b"}

    def test_empty(self):
        assert shell._parse_aliases("") == ([], {})


class TestGateSeesAliases:
    @pytest.fixture(autouse=True)
    def aliases(self, monkeypatch, no_loader):
        monkeypatch.setattr(shell, "_aliases", {
            "rmf": "rm -rf", "gpf": "git push --force", "gs": "git status", "ll": "ls -l",
            "nuke": "ls && rm -rf .git", "rm": "rm -i", "cp": "cp -i", "again": "rmf", "a1": "a2", "a2": "a1",
            "out": "echo x > u",
        })

    def test_hidden_rm(self, repo):
        write(repo / "new.txt")
        assert level("rmf new.txt") == L2
        assert "new.txt" in messages("rmf new.txt")

    def test_hidden_critical(self, repo):
        assert level("rmf /") == L3

    def test_hidden_force_push(self, pushed):
        verdict = judge("gpf")
        assert verdict.level == L3
        assert verdict.word == "force"

    def test_several_commands_in_alias(self, repo):
        assert level("nuke") == L3

    def test_alias_of_alias(self, repo):
        assert level("again /") == L3

    def test_cycle_does_not_hang(self, repo):
        assert level("a1 x") == L0

    def test_redirect_in_alias(self, repo):
        write(repo / "u", "precious\n")
        assert level("out") == L2

    def test_harmless_aliases_pass(self, repo):
        assert level("gs") == L0
        assert level("ll d") == L0

    def test_same_name_alias_keeps_options(self, repo):
        write(repo / "u", "precious\n")
        assert level("cp a u") == L0   # cp -i 라서 명령이 직접 묻는다
        assert level("rm a") == L1     # rm 은 -i 여도 같은 기준으로 판정한다

    def test_after_sudo_alias_is_not_expanded(self, repo):
        assert level("sudo rmf /") == L0  # sudo 는 rmf 라는 명령을 찾다가 실패한다

    def test_after_separator(self, repo):
        assert level("ls && rmf /") == L3

    def test_in_preview_too(self, repo):
        assert gate.preview("rmf /").level == L3

    def test_shown_in_completion(self, repo):
        found = {c.value: c.meta for c in complete.candidates("gp", str(repo), curated=True)}
        assert found["gpf"] == "git push --force"


def _start(cwd, shell_path, home, lines):
    env = dict(os.environ, SHELL=shell_path, HOME=str(home))
    return subprocess.run([sys.executable, "-c", "from cligate.cli import main; main()"], input="\n".join([*lines, ""]),
                          cwd=cwd, env=env, capture_output=True, text=True, timeout=40)


@pytest.mark.parametrize("name, rc", [
    pytest.param("/bin/zsh", ".zshrc", marks=needs_zsh),
    ("/bin/bash", ".bashrc"),
])
class TestRealAliases:
    """실제 설정 파일의 별칭으로 cligate 를 띄운다."""

    @pytest.fixture
    def home(self, tmp_path, name, rc):
        write(tmp_path / "home" / rc, "echo 설정파일-출력\nalias mk='touch made'\nalias rmf='rm -rf'\n")
        return tmp_path / "home"

    def test_alias_runs(self, repo, name, rc, home):
        _start(repo, name, home, ["mk", "exit"])
        assert (repo / "made").exists()

    def test_alias_with_arguments(self, repo, name, rc, home):
        write(repo / "new.txt")
        _start(repo, name, home, ["rmf new.txt", "y", "exit"])
        assert not (repo / "new.txt").exists()

    def test_hidden_danger_is_stopped(self, repo, name, rc, home):
        write(repo / "new.txt")
        result = _start(repo, name, home, ["rmf new.txt", "", "exit"])
        assert (repo / "new.txt").exists()
        assert "new.txt" in result.stdout

    def test_rc_output_not_shown(self, repo, name, rc, home):
        assert "설정파일-출력" not in _start(repo, name, home, ["true", "exit"]).stdout

    def test_exit_code_and_cd(self, repo, name, rc, home):
        result = _start(repo, name, home, ["cd d && pwd", "exit 4"])
        assert result.returncode == 4


def test_sh_needs_no_rc(repo, tmp_path):
    write(tmp_path / "home" / ".zshrc", "alias mk='touch made'\n")
    _start(repo, "/bin/sh", tmp_path / "home", ["mk", "exit"])
    assert not (repo / "made").exists()


def test_broken_rc_does_not_break_cligate(repo, tmp_path):
    write(tmp_path / "home" / ".bashrc", "exit 9\n")
    result = _start(repo, "/bin/bash", tmp_path / "home", ["touch made", "exit"])
    assert (repo / "made").exists()
    assert result.returncode == 0
