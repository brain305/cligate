import os

import pytest
from conftest import git, judge, level, messages, write

from cligate.risk import L0, L1, L2, L3


@pytest.mark.parametrize("line", [
    "ls -la",
    "echo rm",
    "grep rm file",
    "man rm",
    "which rm",
    "command -v rm",
    "git rm a",
    "mytool --clean",
    "./clean.sh",
    'echo "a; rm b"',
    "echo '$(rm a)'",
    'echo "$(date)"',
    "kill $(pgrep node)",
    "ls 2>&1",
    "echo x > rm",
    "ls # rm x; rm y",
    "find . -name x",
    "find . -exec grep a {} \\;",
    "while true; do ls; done",
    "echo $((1+2))",
    "sudo -l",
    "env",
    "rm nofile",
    "rm",
    "rm -rf",
    "sudo rm -rf",
])
def test_harmless_lines_pass(repo, line):
    assert level(line) == L0


@pytest.mark.parametrize("line", [
    "(rm new.txt)",
    "{ rm new.txt; }",
    "if true; then rm new.txt; fi",
    "! rm new.txt",
    "sudo rm new.txt",
    "sudo -u root rm new.txt",
    "env -u X rm new.txt",
    "/bin/rm new.txt",
    "\\rm new.txt",
    "FOO=1 rm new.txt",
    "time rm new.txt",
    "nohup rm new.txt &",
    'sh -c "rm new.txt"',
    'bash -lc "rm new.txt"',
    'eval "rm new.txt"',
    "(rm new.txt); ls",
    'echo "$(rm new.txt)"',
    "echo `rm new.txt`",
    "echo a#b; rm new.txt",
    "ls; rm new.txt",
    "rm new.txt 2>&1",
    "rm -- new.txt",
])
def test_rm_is_found_wherever_it_is(repo, line):
    write(repo / "new.txt")
    assert level(line) == L2


@pytest.mark.parametrize("line", [
    "ls | xargs rm",
    "echo a | xargs -n1 rm",
    "echo a | xargs -I {} rm {}",
    "find . -exec rm {} \\;",
    "find . -name x -exec rm {} +",
    "find . -delete",
    "rm $X",
    "rm $X/foo",
    "rm $(cat list)",
])
def test_unknown_targets_ask(repo, line):
    assert level(line) == L2


@pytest.mark.parametrize("line", [
    "$(echo rm) a.txt",
    "`echo rm` a.txt",
    "X=rm; $X a.txt",
    "sudo $CMD a",
    'sh -c "$CMD"',
    'eval "$CMD"',
])
def test_unclear_command_notifies_and_runs(repo, line):
    assert level(line) == L1
    assert "확인하지 못했습니다" in messages(line)


class TestRmAgainstGit:
    def test_committed_file(self, repo):
        assert level("rm a") == L1
        assert "git restore" in messages("rm a")

    def test_committed_directory(self, repo):
        assert level("rm -rf d") == L1

    def test_untracked(self, repo):
        write(repo / "new.txt")
        assert level("rm new.txt") == L2
        assert "git에 없음" in messages("rm new.txt")

    def test_modified(self, repo):
        write(repo / "a", "changed\n")
        assert "커밋 안 한 변경" in messages("rm a")
        assert level("rm a") == L2

    def test_staged_new_file(self, repo):
        write(repo / "st")
        git("add", "st")
        assert level("rm st") == L2

    def test_ignored(self, repo):
        write(repo / "x.tmp")
        assert "무시" in messages("rm x.tmp")
        assert level("rm x.tmp") == L2

    def test_already_deleted_file_has_nothing_to_lose(self, repo):
        os.remove(repo / "a")
        assert level("rm a") == L0

    def test_mixed_glob_lists_only_the_lost(self, repo):
        write(repo / "logs" / "u.log")
        text = messages("rm logs/*.log")
        assert "u.log" in text
        assert "1.log" not in text
        assert "커밋된 파일 2개" in text

    def test_outside_repo(self, tmp_path, monkeypatch):
        write(tmp_path / "out" / "f")
        monkeypatch.chdir(tmp_path / "out")
        assert level("rm f") == L2

    def test_cd_changes_where_targets_are(self, repo):
        assert level("cd d && rm x") == L1
        assert level("rm x") == L0

    def test_space_in_name(self, repo):
        write(repo / "with space.txt")
        assert level('rm "with space.txt"') == L2


class TestScope:
    def test_many_committed_files_ask(self, repo):
        for i in range(60):
            write(repo / f"f{i}.txt")
        git("add", "-A")
        git("commit", "-qm", "many")
        assert level("rm f*.txt") == L2

    def test_many_untracked_files_need_word(self, repo):
        for i in range(60):
            write(repo / f"f{i}.txt")
        assert level("rm f*.txt") == L3

    def test_large_untracked_file_needs_word(self, repo):
        with open(repo / "big.bin", "wb") as out:
            out.truncate(120 * 1024 * 1024)
        assert level("rm big.bin") == L3

    def test_listing_is_cut_and_sorted(self, repo):
        for i in range(20):
            write(repo / f"f{i:02}.txt")
        text = messages("rm f*.txt")
        assert "외 12개" in text
        assert text.index("f00.txt") < text.index("f07.txt")


@pytest.mark.parametrize("line", [
    "rm -rf /",
    "rm -rf /*",
    "sudo rm -rf /",
    'sh -c "rm -rf /"',
    "rm -rf ~",
    "rm -rf ~/",
    "rm -rf ~/*",
    "rm -rf $HOME",
    "cd ~ && rm -rf *",
    "cd / && rm -rf *",
    "rm -rf $NOPE/",
    "rm -rf $NOPE/*",
    'rm -rf "$NOPE/"',
    "rm -rf /usr",
    "rm -rf /etc",
    "rm -rf .git",
    "rm -rf .git/config",
    "rm -rf .",
    "find / -delete",
    "find ~ -name a -delete",
    "rm a && rm -rf /",
])
def test_always_needs_word(repo, line):
    verdict = judge(line)
    assert verdict.level == L3
    assert verdict.word == "delete"


@pytest.mark.parametrize("line", ["rm -rf ~/nofile", "rm -rf /usr/local/nofile", "rm -rf /tmp/cligate-nofile"])
def test_missing_paths_under_critical_dirs_pass(repo, line):
    assert level(line) == L0


@pytest.mark.parametrize("line", [
    "rm -rf /mnt/c",
    "rm -rf /mnt/c/",
    "rm -rf /mnt/c/*",
    "rm -rf /mnt/d",
    "rm -rf /mnt/c/Users",
    "rm -rf /mnt/c/users",
    "rm -rf /mnt/c/Users/me",
    "rm -rf /mnt/c/Users/me/*",
    "rm -rf /mnt/c/Windows",
    "rm -rf '/mnt/c/Program Files'",
    "rm -rf '/mnt/c/Program Files (x86)'",
    "rm -rf /mnt/c/ProgramData",
    "sudo rm -rf /mnt/c/Users",
])
def test_windows_folders_from_wsl_need_word(repo, line):
    verdict = judge(line)
    assert verdict.level == L3
    assert verdict.word == "delete"


@pytest.mark.parametrize("line", [
    "rm -rf /mnt/data",
    "rm -rf /mnt/c/Users/me/project/old",
    "rm -rf /mnt/c/temp",
])
def test_other_paths_under_mnt_are_not_critical(repo, line):
    assert judge(line).level != L3


def test_root_is_shown_as_root_from_anywhere(repo, monkeypatch):
    assert "    /  (" in messages("rm -rf /")
    monkeypatch.chdir("/")
    assert "    /  (" in messages("rm -rf /")


def test_current_folder_is_shown_by_full_path(repo):
    assert f"    {os.path.realpath(repo)}  (" in messages("rm -rf .")


def test_repo_named_from_outside(repo, monkeypatch):
    monkeypatch.chdir(repo.parent)
    assert level("rm -rf repo") == L3
    assert level("rm -rf repo/d") == L1
