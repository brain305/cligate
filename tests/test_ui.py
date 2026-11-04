from types import SimpleNamespace

import pytest
from prompt_toolkit.utils import get_cwidth

from cligate import risk, ui
from cligate.risk import Finding, L0, L1, L2, L3


def box_lines(title, body):
    text = "".join(part for _, part in ui._box(title, body, ""))
    return text.split("\n")


class TestBox:
    def test_all_lines_same_width(self):
        lines = box_lines("[L2] 커밋 안 한 변경을 잃습니다", ["src/app.py  (커밋 안 한 변경)", "규모: 파일 1개, 4B"])
        assert lines[0].startswith("╭─ [L2]")
        assert lines[-1].startswith("╰")
        assert len({get_cwidth(line) for line in lines}) == 1

    def test_long_korean_wraps_inside(self):
        lines = box_lines("제목", ["가" * 300])
        assert len(lines) > 4
        assert len({get_cwidth(line) for line in lines}) == 1

    def test_long_title_wraps_inside(self):
        lines = box_lines("제목" * 100, [])
        assert len({get_cwidth(line) for line in lines}) == 1

    def test_never_wider_than_terminal(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "40")
        assert all(get_cwidth(line) <= 40 for line in box_lines("제목", ["내용" * 50]))

    def test_narrow_terminal_does_not_crash(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "5")
        assert box_lines("제목", ["내용"])

    def test_capped_on_wide_terminal(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "300")
        assert get_cwidth(box_lines("제목", [])[0]) == ui.MAX_BOX_WIDTH


class TestWrap:
    @pytest.mark.parametrize("text, width, expected", [
        ("abcd", 2, ["ab", "cd"]),
        ("가나", 2, ["가", "나"]),
        ("가a", 2, ["가", "a"]),
        ("", 5, [""]),
        ("ab cd ef", 5, ["ab cd", "ef"]),
        ("가나 다라", 6, ["가나", "다라"]),
        ("  abcdef", 4, ["  ab", "cdef"]),
    ])
    def test_wrap(self, text, width, expected):
        assert ui._wrap(text, width) == expected


class TestShow:
    @pytest.mark.parametrize("level, box", [(L1, False), (L2, True), (L3, True)])
    def test_box_only_when_asking(self, level, box, monkeypatch):
        printed = []
        monkeypatch.setattr(ui, "_print", printed.extend)
        ui.show(Finding(level, "제목\n    내용"))
        out = "".join(part for _, part in printed)
        assert ("╭" in out) == box
        assert f"[L{level}]" in out
        assert "제목" in out and "내용" in out


class TestFrame:
    def prompt(self, level=L0):
        screen = ui.Prompt("cligate", SimpleNamespace())
        screen.line_risk.level = level
        return screen

    def test_border_uses_terminal_color_without_selected_risk(self):
        assert self.prompt()._border_style() == ui.FRAME == ""

    @pytest.mark.parametrize("level, color", [(L1, "ansicyan"), (L2, "ansiyellow"), (L3, "ansired")])
    def test_border_follows_selected_level(self, level, color):
        assert color in self.prompt(level)._border_style()

    @pytest.mark.parametrize("level, expected", [
        (L0, "cligate"),
        (L1, "cligate · L1 추정"),
        (L2, "cligate · L2 추정"),
        (L3, "cligate · L3 추정"),
    ])
    def test_title_names_the_level(self, level, expected):
        assert self.prompt(level)._frame_title("cligate", 80) == expected

    def test_narrow_terminal_keeps_plain_title(self):
        assert self.prompt(L3)._frame_title("cligate", 20) == "cligate"

    def test_read_command_clears_previous_level(self, monkeypatch):
        screen = self.prompt(L3)
        monkeypatch.setattr(screen, "_run", lambda *a, **k: None)
        screen.read_command()
        assert screen.line_risk.level == L0


class TestMenu:
    def state(self, index):
        return SimpleNamespace(completions=[1, 2, 3], complete_index=index)

    def test_hint_before_selection(self):
        assert "3개" in ui._menu_hint(self.state(None))[0][1]

    def test_hint_shows_position(self):
        assert "2/3" in ui._menu_hint(self.state(1))[0][1]

    def test_no_hint_without_list(self):
        assert ui._menu_hint(None) == []

    @pytest.mark.parametrize("menu, color", [
        ("class:completion-menu.meta.completion", "f0716a"),
        ("class:completion-menu.meta.completion.current", "ffd9d6"),
    ])
    def test_risk_meta_stays_readable_when_selected(self, menu, color):
        attrs = ui.MENU_STYLE.get_attrs_for_style_str(f"{menu} class:risk-l3")
        assert attrs.color == color


def test_short_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "a").mkdir()
    monkeypatch.chdir(tmp_path / "a")
    real = str((tmp_path / "a").resolve())
    assert ui._short_cwd() in ("~/a", real)


class TestRealTerminal:
    """가상 터미널에 실제 cligate 를 띄워, 전체 화면을 쓰지 않아 지켜지는 동작을 확인한다."""

    def _spawn(self, cwd):
        import os
        import pty
        import sys
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(cwd)
            os.environ.update(PROMPT_TOOLKIT_NO_CPR="1", TERM="xterm")
            os.execv(sys.executable, [sys.executable, "-c", "from cligate.cli import main; main()"])
        return pid, fd

    def _read(self, fd, seconds):
        import os
        import select
        import time
        out = b""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    break
        return out.decode("utf-8", "replace")

    def _quit(self, pid, fd):
        import os
        os.write(fd, b"\x15\x04")
        self._read(fd, 0.4)
        _, status = os.waitpid(pid, 0)
        os.close(fd)
        return status

    def test_full_screen_program_gets_terminal(self, repo):
        import os
        script = repo / "check_term.py"
        script.write_text("import os, sys\nopen('tty.txt', 'w').write(str(os.isatty(0)) + str(os.isatty(1)))\n")
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"python3 check_term.py\r")
        self._read(fd, 1.5)
        self._quit(pid, fd)
        assert (repo / "tty.txt").read_text() == "TrueTrue"

    def test_program_can_read_keyboard(self, repo):
        import os
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"read answer && echo \"$answer\" > got.txt\r")
        self._read(fd, 1.0)
        os.write(fd, "한글답변\r".encode())
        self._read(fd, 1.0)
        self._quit(pid, fd)
        assert (repo / "got.txt").read_text().strip() == "한글답변"

    def test_ctrl_c_clears_line_and_keeps_running(self, repo):
        import os
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"touch cancelled\x03")
        self._read(fd, 0.5)
        os.write(fd, b"touch made\r")
        self._read(fd, 1.0)
        self._quit(pid, fd)
        assert not (repo / "cancelled").exists()
        assert (repo / "made").exists()

    def test_ctrl_c_stops_running_command(self, repo):
        import os
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"sleep 30; touch finished\r")
        self._read(fd, 0.8)
        os.write(fd, b"\x03")
        self._read(fd, 0.8)
        os.write(fd, b"touch alive\r")
        self._read(fd, 1.0)
        self._quit(pid, fd)
        assert (repo / "alive").exists()

    def test_up_arrow_recalls_history(self, repo):
        import os
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"touch again\r")
        self._read(fd, 1.0)
        os.remove(repo / "again")
        os.write(fd, b"\x1b[A\r")
        self._read(fd, 1.0)
        self._quit(pid, fd)
        assert (repo / "again").exists()

    def test_ctrl_d_exits_only_on_empty_line(self, repo):
        import os
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"touch kept\x04")
        self._read(fd, 0.5)
        os.write(fd, b"\r")
        self._read(fd, 1.0)
        status = self._quit(pid, fd)
        assert (repo / "kept").exists()
        assert os.waitstatus_to_exitcode(status) == 0

    def test_tab_opens_list_and_inserts(self, repo):
        import os
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"touch made; ech")
        self._read(fd, 1.0)
        os.write(fd, b"\t\r")
        self._read(fd, 1.0)
        self._quit(pid, fd)
        assert (repo / "made").exists()

    def test_box_is_drawn_and_erased(self, repo):
        import os
        pid, fd = self._spawn(repo)
        screen = self._read(fd, 1.5)
        self._quit(pid, fd)
        assert "╭─ cligate" in screen and "╰" in screen
