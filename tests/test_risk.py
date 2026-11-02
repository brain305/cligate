import pytest
from conftest import judge, level, write

from cligate import risk
from cligate.risk import L0, L1, L2, L3, R0, R1, R2, R3, S0, S1, S2


@pytest.mark.parametrize("r, s, expected", [
    (R0, S0, L0), (R0, S1, L0), (R0, S2, L0),
    (R1, S0, L0), (R1, S1, L1), (R1, S2, L1),
    (R2, S0, L1), (R2, S1, L1), (R2, S2, L2),
    (R3, S0, L2), (R3, S1, L2), (R3, S2, L3),
])
def test_matrix_matches_report(r, s, expected):
    assert risk.judge(r, s, "m").level == expected


def test_measure_empty(tmp_path):
    assert risk.measure([])[0] == S0


def test_measure_small(tmp_path):
    f = tmp_path / "one"
    f.write_text("x")
    assert risk.measure([str(f)])[0] == S1


def test_measure_file_count_boundary(tmp_path):
    files = []
    for i in range(50):
        f = tmp_path / f"f{i}"
        f.write_text("x")
        files.append(str(f))
    assert risk.measure(files[:49])[0] == S1
    assert risk.measure(files)[0] == S2


def test_measure_size_boundary(tmp_path):
    f = tmp_path / "big"
    with open(f, "wb") as out:
        out.truncate(risk.MAX_BYTES)
    assert risk.measure([str(f)])[0] == S2
    with open(f, "wb") as out:
        out.truncate(risk.MAX_BYTES - 1)
    assert risk.measure([str(f)])[0] == S1


def test_measure_walks_directories(tmp_path):
    d = tmp_path / "d"
    for i in range(60):
        write(d / "sub" / f"f{i}")
    assert risk.measure([str(d)])[0] == S2


def test_measure_counts_each_path_once(tmp_path):
    f = tmp_path / "one"
    f.write_text("x")
    assert risk.measure([str(f)] * 60)[0] == S1


def test_verdict_takes_highest_level():
    verdict = risk.Verdict([risk.Finding(L1, "a"), risk.Finding(L3, "b", "force"), risk.Finding(L2, "c")])
    assert verdict.level == L3
    assert verdict.word == "force"


def test_empty_verdict_is_l0():
    assert risk.Verdict().level == L0
    assert risk.Verdict().word is None


# 보고서 2.2.3 의 판정 예시 6개
class TestReportExamples:
    def test_rm_all_committed(self, repo):
        assert level("rm logs/*.log") == L1

    def test_rm_uncommitted_and_large(self, repo):
        write(repo / "logs" / "u.log")
        with open(repo / "logs" / "huge.log", "wb") as out:
            out.truncate(340 * 1024 * 1024)
        verdict = judge("rm logs/*.log")
        assert verdict.level == L3
        assert verdict.word == "delete"

    def test_rm_empty_variable(self, repo):
        assert level("rm -rf $BUILD/") == L3

    def test_reset_hard_clean(self, repo):
        assert level("git reset --hard") == L0

    def test_reset_hard_three_changes(self, repo):
        for name in ("a", "b", "d/x"):
            write(repo / name, "changed\n")
        assert level("git reset --hard") == L2

    def test_force_push_remote_ahead(self, pushed):
        verdict = judge("git push --force")
        assert verdict.level == L3
        assert verdict.word == "force"
