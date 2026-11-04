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
DIM = "fg:ansibrightblack"
MENU_STYLE = Style.from_dict({
    "completion-menu": "bg:#1c2027 #e6e8ec",
    "completion-menu.completion.current": "bg:#2c3a3c #ffffff",
    "completion-menu.meta.completion": "bg:#1c2027 #98a1b0",
    "completion-menu.meta.completion.current": "bg:#2c3a3c #c9ced6",
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

    def read_command(self):
        self.command.reset()
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
        def top():
            return [(DIM, f"╭─ {title} " + "─" * max(0, _columns() - get_cwidth(title) - 5) + "╮")]

        def bottom():
            return [(DIM, "╰" + "─" * max(0, _columns() - 2) + "╯")]

        edit = Window(BufferControl(buffer, input_processors=[BeforeInput("> ")]), wrap_lines=True,
                      dont_extend_height=True)
        middle = VSplit([Window(width=1, char="│", style=DIM), Window(width=1, char=" "), edit,
                         Window(width=1, char=" "), Window(width=1, char="│", style=DIM)])
        status_line = Window(FormattedTextControl(self._status_parts), height=1, dont_extend_height=True)
        return Layout(HSplit([
            Window(FormattedTextControl(top), height=1, dont_extend_height=True),
            middle,
            Window(FormattedTextControl(bottom), height=1, dont_extend_height=True),
            ConditionalContainer(status_line, Condition(lambda: status) & ~has_completions),  # 목록이 그 자리를 쓴다
            CompletionsMenu(max_height=MENU_HEIGHT, scroll_offset=1),
        ]), focused_element=edit)

    def _status_parts(self):
        parts = self.monitor.status_parts()
        return [("", "  "), *parts, (DIM, ("  ·  " if parts else "") + _short_cwd())]


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
