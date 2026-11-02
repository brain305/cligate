import os

import pytest
from conftest import git, judge, level, messages, write

from cligate.risk import L0, L1, L2, L3


@pytest.fixture
def files(repo):
    """a, b 는 커밋됨 / u, v 는 미추적 / m 은 수정됨 / empty 는 빈 파일 / d 는 커밋된 폴더 / x.tmp 는 무시됨."""
    write(repo / "u", "untracked\n")
    write(repo / "v", "untracked2\n")
    write(repo / "m-base")
    git("add", "m-base")
    git("commit", "-qm", "m")
    write(repo / "m-base", "changed\n")
    (repo / "empty").write_text("")
    write(repo / "x.tmp", "ignored\n")
    write(repo / "out" / "u", "already\n")
    write(repo / "out" / "only", "only\n")
    return repo


class TestMvCp:
    @pytest.mark.parametrize("cmd", ["mv", "cp"])
    @pytest.mark.parametrize("line, expected", [
        ("{} u brand-new", L0),                    # 덮어쓸 것이 없다
        ("{} u a", L1),                            # 커밋되어 있어 되살릴 수 있다
        ("{} a u", L2),                            # 미추적 파일을 덮어쓴다
        ("{} a m-base", L2),                       # 커밋 안 한 변경을 덮어쓴다
        ("{} a x.tmp", L2),                        # 무시된 파일도 되살릴 수 없다
        ("{} a empty", L0),                        # 빈 파일은 잃을 것이 없다
        ("{} u out", L2),                          # 폴더 안의 같은 이름 파일
        ("{} a out", L0),                          # 폴더 안에 같은 이름이 없다
        ("{} u out/", L2),
        ("{} -f a u", L2),
        ("{} -v a u", L2),
        ("{} -n a u", L0),                         # 덮어쓰지 않는다
        ("{} --no-clobber a u", L0),
        ("{} -i a u", L0),                         # 명령이 직접 묻는다
        ("{} -b a u", L0),                         # 백업을 남긴다
        ("{} --backup=numbered a u", L0),
        ("{} -t out u", L2),
        ("{} --target-directory=out u", L2),
        ("{} -t out a", L0),
        ("{} -- a u", L2),
        ("{} u", L0),                              # 대상이 없으면 스스로 실패한다
        ("{} nosuch u", L0),                       # 원본이 없으면 스스로 실패한다
        ("{} u u", L0),                            # 같은 파일
        ("{} a b u", L2),
        ("sudo {} a u", L2),
        ("cd out && {} only u", L2),
        ("{} a $(cat list)", L2),                  # 대상을 알 수 없다
        ("{} a $NOPE", L2),
        ("{} $(cat list) out", L2),
        ("{} $(cat list) u", L2),                  # 원본은 몰라도 덮어쓸 파일은 안다
        ("{} $(cat list) brand-new", L0),
        ("ls | xargs {} -t out", L2),
    ])
    def test_levels(self, files, cmd, line, expected):
        assert level(line.format(cmd)) == expected

    def test_variable_is_filled(self, files, monkeypatch):
        monkeypatch.setenv("TARGET", str(files / "u"))
        assert level("cp a $TARGET") == L2

    def test_glob_sources(self, files):
        write(files / "new1.txt")
        write(files / "new2.txt")
        write(files / "out" / "new1.txt", "there\n")
        assert level("cp new*.txt out") == L2
        assert "new1.txt" in messages("cp new*.txt out")
        assert "new2.txt" not in messages("cp new*.txt out")

    def test_lists_what_is_lost(self, files):
        assert "u  (git에 없음)" in messages("cp a u")
        assert "규모: 파일 1개" in messages("cp a u")

    def test_committed_message(self, files):
        assert "git restore" in messages("cp u a")

    def test_git_internal_needs_word(self, files):
        verdict = judge("cp a .git/config")
        assert verdict.level == L3
        assert verdict.word == "delete"

    def test_large_overwrite_needs_word(self, files):
        with open(files / "big.bin", "wb") as out:
            out.truncate(120 * 1024 * 1024)
        assert level("cp a big.bin") == L3

    def test_judging_changes_nothing(self, files):
        judge("mv a u")
        assert (files / "a").exists()
        assert (files / "u").read_text() == "untracked\n"


class TestCpRecursive:
    def test_overlap_found(self, files):
        write(files / "src" / "u", "new\n")
        write(files / "src" / "fresh", "new\n")
        assert level("cp -r src/u out") == L2
        os.rename(files / "out", files / "dst")
        write(files / "dst" / "src" / "u", "there\n")
        assert level("cp -r src dst") == L2
        assert "dst/src/u" in messages("cp -r src dst")
        assert "fresh" not in messages("cp -r src dst")

    def test_flags(self, files):
        write(files / "src" / "f", "new\n")
        write(files / "dst" / "src" / "f", "there\n")
        for flag in ("-r", "-R", "-a", "-rf", "--recursive", "--archive"):
            assert level(f"cp {flag} src dst") == L2

    def test_no_overlap(self, files):
        write(files / "src" / "f", "new\n")
        write(files / "dst" / "other", "there\n")
        assert level("cp -r src dst") == L0

    def test_without_recursive_cp_fails_by_itself(self, files):
        write(files / "src" / "f", "new\n")
        write(files / "dst" / "src" / "f", "there\n")
        assert level("cp src dst") == L0

    def test_mv_directory_does_not_merge(self, files):
        write(files / "src" / "f", "new\n")
        write(files / "dst" / "src" / "f", "there\n")
        assert level("mv src dst") == L0

    def test_no_target_directory_flag(self, files):
        write(files / "src" / "f", "new\n")
        write(files / "dst" / "f", "there\n")
        assert level("cp -rT src dst") == L2

    def test_many_overlaps_need_word(self, files):
        for i in range(60):
            write(files / "src" / f"f{i}", "new\n")
            write(files / "dst" / "src" / f"f{i}", "there\n")
        assert level("cp -r src dst") == L3


class TestMvCritical:
    @pytest.mark.parametrize("line", ["mv .git elsewhere", "mv ~ elsewhere"])
    def test_moving_important_folder_asks(self, files, line):
        assert level(line) == L2
        assert "중요한 폴더" in messages(line)

    def test_cp_from_there_is_fine(self, files):
        assert level("cp ~ elsewhere") == L0

    def test_renaming_ordinary_folder_is_quiet(self, files):
        assert level("mv d e") == L0


class TestRedirect:
    @pytest.mark.parametrize("line, expected", [
        ("echo x > brand-new", L0),
        ("echo x > a", L1),
        ("echo x > u", L2),
        ("echo x > m-base", L2),
        ("echo x > x.tmp", L2),
        ("echo x > empty", L0),
        ("echo x >| u", L2),
        ("ls &> u", L2),
        ("ls 2> u", L2),
        ("ls >& u", L2),
        ("echo x>u", L2),
        ("> u", L2),
        ("echo x >> u", L0),                       # 덧붙이기
        ("ls &>> u", L0),
        ("ls 2>&1", L0),
        ("ls >&2", L0),
        ("ls > /dev/null", L0),
        ("ls 2> /dev/null", L0),
        ("ls > /dev/null 2>&1", L0),
        ("cat < u", L0),                           # 읽기
        ("cat <<EOF", L0),
        ('echo "a > u"', L0),                      # 따옴표 안은 글자이다
        ("echo x > d", L0),                        # 폴더에는 쓸 수 없어 셸이 거부한다
        ("echo x > $(cat name)", L2),
        ("(ls) > u", L2),
        ("ls | sort > u", L2),
        ("cd out && ls > u", L2),
        ("cd out && ls > brand-new", L0),
        ("if true; then ls > u; fi", L2),
        ('sh -c "ls > u"', L2),
        ("sudo ls > u", L2),
    ])
    def test_levels(self, files, line, expected):
        assert level(line) == expected

    def test_message(self, files):
        assert "u  (git에 없음)" in messages("echo x > u")
        assert "git restore" in messages("echo x > a")

    def test_large_file_needs_word(self, files):
        with open(files / "big.log", "wb") as out:
            out.truncate(120 * 1024 * 1024)
        verdict = judge("echo x > big.log")
        assert verdict.level == L3

    def test_highest_of_the_line(self, files):
        assert level("cp a brand-new > u") == L2
        assert level("cp a u > a") == L2


class TestOutsideRepo:
    def test_existing_file_asks(self, tmp_path, monkeypatch):
        write(tmp_path / "out" / "f", "data\n")
        monkeypatch.chdir(tmp_path / "out")
        assert level("echo x > f") == L2
        assert level("cp f g") == L0


@pytest.mark.parametrize("line, expected", [
    ("echo x > u", L2),
    ("cp a u", L2),
    ("cp a brand-new", L0),
])
def test_preview_matches_execution(files, line, expected):
    from cligate import gate
    assert gate.preview(line).level == judge(line).level == expected
