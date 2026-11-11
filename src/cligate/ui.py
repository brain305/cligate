"""화면을 그린다. 전체 화면을 쓰지 않아 명령 출력, 스크롤, vim 은 터미널이 그대로 처리한다.

입력창은 입력받는 동안에만 그리고 지운다. 명령은 그 자리에서 터미널에 직접 출력한다.
"""
import os
import shutil

from prompt_toolkit import print_formatted_text
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import ConditionalContainer, FormattedTextControl, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from . import complete, risk, statusline

MAX_BOX_WIDTH = 100
MENU_HEIGHT = 8
MIN_GAP = 3   # 제목과 현재 폴더 사이에 남길 최소 테두리
MIN_CWD = 8   # 이보다 좁게 줄여야 하면 폴더를 쓰지 않는다
DIM = "fg:ansibrightblack"
FRAME = ""  # 터미널 기본 글자색. 어두운 배경에서는 밝게, 밝은 배경에서는 어둡게 보인다
MENU_STYLE = Style.from_dict({
    "completion-menu": "bg:#1c2027 #e6e8ec",
    "completion-menu.completion.current": "bg:#0f6b74 #ffffff bold",
    "completion-menu.meta.completion": "bg:#1c2027 #98a1b0",
    "completion-menu.meta.completion.current": "bg:#0f6b74 #e3f4f6",
    "completion-menu.meta.completion risk-l2": "#e5c25c",
    "completion-menu.meta.completion risk-l3": "#f0716a",
    "completion-menu.meta.completion.current risk-l2": "#fff1c7",  # 청록 배경에서도 읽히도록 밝게
    "completion-menu.meta.completion.current risk-l3": "#ffd9d6",
})


def show(finding):
    """판정 결과를 보여준다. L1 은 줄로, 확인이 필요한 L2 와 L3 는 눈에 띄게 상자로 그린다."""
    title, *body = str(finding).split("\n")
    style = risk.LEVEL_STYLE.get(finding.level, "")
    if finding.level < risk.L2:
        _print([(style, title), *[("", f"\n{line}") for line in body]])
        return
    _print(_box(title, [line.removeprefix("    ") for line in body], style))


def notice(text):
    _print([(DIM, text)])


def answer(text):
    """확인창은 지워지므로 뭐라고 답했는지 남긴다."""
    if text:
        _print([(DIM, f"  입력: {text}")])


def echo(line):
    """입력창을 지운 자리에 실행한 명령을 남긴다."""
    _print([(DIM, "❯ "), ("", line)])


def _print(parts):
    print_formatted_text(FormattedText(parts))


def _box(title, body, style):
    width = min(shutil.get_terminal_size((80, 24)).columns, MAX_BOX_WIDTH)
    inner = max(width - 4, 20)
    lines = [wrapped for line in body for wrapped in _wrap(line, inner)]
    head = _wrap(title, inner - 3)
    top = f"╭─ {head[0]} " + "─" * max(0, inner - get_cwidth(head[0]) - 1) + "╮"
    parts = [(style, top)]
    for line in [*head[1:], *lines]:
        parts += [(style, "\n│ "), ("", line + " " * (inner - get_cwidth(line))), (style, " │")]
    return [*parts, (style, "\n╰" + "─" * (inner + 2) + "╯")]


def _wrap(text, width):
    """글자 폭 기준으로 줄을 나눈다. 한글은 두 칸을 차지한다. 되도록 띄어쓰기에서 끊는다."""
    lines, current = [], ""
    for char in text:
        if get_cwidth(current + char) > width:
            if char == " ":
                lines.append(current)
                current = ""
                continue
            head, space, tail = current.rpartition(" ")
            if space and head.strip():
                lines.append(head)
                current = tail
            else:
                lines.append(current)
                current = ""
        current += char
    return [*lines, current]


class Prompt:
    def __init__(self, name, monitor):
        self.name = name
        self.monitor = monitor
        self.command = Buffer(completer=complete.completer(), complete_while_typing=False, history=InMemoryHistory(),
                              multiline=False, accept_handler=_finish)
        self.command.on_text_changed += complete.IdleTrigger()
        self.line_risk = complete.LineRisk()
        self.command.on_text_changed += self.line_risk

    def read_command(self):
        self.command.reset()
        self.line_risk.reset()
        return self._run(self.command, self.name, complete.key_bindings(), status=True)

    def ask(self, title):
        answer = Buffer(multiline=False, accept_handler=_finish)
        return self._run(answer, title, KeyBindings(), status=False)

    def _run(self, buffer, title, extra_bindings, status):
        app = Application(layout=self._layout(buffer, title, status),
                          key_bindings=merge_key_bindings([_bindings(buffer), extra_bindings]),
                          style=MENU_STYLE, erase_when_done=True, refresh_interval=statusline.REDRAW_SECONDS)
        self.monitor.on_change = app.invalidate
        try:
            return app.run()
        finally:
            self.monitor.on_change = None

    def _layout(self, buffer, title, status):
        def border():
            return self._border_style() if status else FRAME

        def top():
            columns = _columns()
            if not status:
                return _top_line(title, border(), "", columns)
            return _top_line(self._frame_title(title, columns), border(), _short_cwd(), columns)

        def bottom():
            return [(border(), "╰" + "─" * max(0, _columns() - 2) + "╯")]

        def symbol():
            style = border()
            return [(f"{style} bold" if style else "", "❯ ")]

        edit = Window(BufferControl(buffer, input_processors=[BeforeInput(symbol)]), wrap_lines=True,
                      dont_extend_height=True)
        middle = VSplit([Window(width=1, char="│", style=border), Window(width=1, char=" "), edit,
                         Window(width=1, char=" "), Window(width=1, char="│", style=border)])
        status_line = Window(FormattedTextControl(self._status_parts), height=1, dont_extend_height=True)
        menu_hint = Window(FormattedTextControl(lambda: _menu_hint(buffer.complete_state)), height=1,
                           dont_extend_height=True)
        return Layout(HSplit([
            Window(FormattedTextControl(top), height=1, dont_extend_height=True),
            middle,
            Window(FormattedTextControl(bottom), height=1, dont_extend_height=True),
            ConditionalContainer(status_line, Condition(lambda: status) & ~has_completions),  # 목록이 그 자리를 쓴다
            CompletionsMenu(max_height=MENU_HEIGHT, scroll_offset=1),
            ConditionalContainer(menu_hint, Condition(lambda: status) & has_completions),
        ]), focused_element=edit)

    def _border_style(self):
        """입력 중인 줄의 등급 색을 쓴다. L0 이면 기본 색이다."""
        return risk.LEVEL_STYLE.get(self.line_risk.level, FRAME)

    def _frame_title(self, title, columns):
        """색을 구분하기 어려워도 등급을 읽을 수 있게 제목에 쓴다. 창이 좁으면 원래 제목만 쓴다.

        원격을 확인하지 않은 값이라 엔터 뒤의 등급과 다를 수 있다. 그래서 추정이라고 밝힌다.
        """
        level = self.line_risk.level
        if level < risk.L1:
            return title
        text = f"{title} · L{level} 추정"
        return text if get_cwidth(text) + 5 <= columns else title

    def _status_parts(self):
        parts = self.monitor.status_parts()
        return [("", "  "), *parts] if parts else []  # 현재 폴더는 윗줄 테두리에 쓴다


def _top_line(title, style, cwd, columns):
    """입력창 윗줄. cd 로 옮겨도 위치를 알 수 있게 오른쪽 테두리 사이에 현재 폴더를 흐리게 쓴다.

    자리가 모자라면 폴더 경로의 앞을 줄이고, 그래도 모자라면 쓰지 않는다. 제목이 먼저다.
    """
    left = f"╭─ {title} "
    room = columns - get_cwidth(left) - MIN_GAP - 4  # 오른쪽 "␣경로␣─╮" 의 고정 4칸
    cwd = _fit_left(cwd, room) if cwd else ""
    if not cwd:
        return [(style, left + "─" * max(0, columns - get_cwidth(left) - 1) + "╮")]
    gap = columns - get_cwidth(left) - get_cwidth(cwd) - 4
    return [(style, left + "─" * gap + " "), (DIM, cwd), (style, " ─╮")]


def _fit_left(text, width):
    """폭에 맞게 앞쪽을 …로 줄인다. 끝 폴더 이름이 가장 중요하다. 너무 좁으면 빈 문자열."""
    if get_cwidth(text) <= width:
        return text
    if width < MIN_CWD:
        return ""
    tail = text
    while tail and get_cwidth("…" + tail) > width:
        tail = tail[1:]
    return "…" + tail


def _menu_hint(state):
    """목록 아래의 위치와 키 안내."""
    if state is None:
        return []
    total = len(state.completions)
    if state.complete_index is None:
        return [(DIM, f" {total}개  ·  Tab 선택")]
    return [(DIM, f" {state.complete_index + 1}/{total}  ·  Tab 다음  ·  Space 확정")]


def _finish(buffer):
    get_app().exit(result=buffer.text)
    return True  # 다음 입력 전에 비우므로 여기서는 그대로 둔다


def _bindings(buffer):
    bindings = KeyBindings()

    @bindings.add("c-c")
    def interrupt(event):
        event.app.exit(exception=KeyboardInterrupt)

    @bindings.add("c-d", filter=Condition(lambda: not buffer.text))
    def end(event):
        event.app.exit(exception=EOFError)

    return bindings


def _columns():
    return get_app().output.get_size().columns


def _short_cwd():
    try:
        cwd = os.getcwd()
    except OSError:
        return ""
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd == home or cwd.startswith(home + os.sep) else cwd
