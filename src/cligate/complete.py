import asyncio
import os
import re
import shlex
import time
from dataclasses import dataclass

from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import CompleteEvent, Completer, Completion, ThreadedCompleter
from prompt_toolkit.filters import completion_is_selected, has_completions
from prompt_toolkit.key_binding import KeyBindings

from . import gate, gitspec, learn, parse, shell, state, statusline
from .risk import L2

IDLE_SECONDS = 0.5
BUDGET_SECONDS = 0.3  # 넘기면 자동으로 띄우지 않는다. TAB 은 기다린다. 강제 푸시 판정에 110ms 쯤 걸린다
MAX_CANDIDATES = 200
MARK = "⚠"

# 1단계: 기본 순서. git 명령의 순서와 설명은 gitspec.py 에 있다
COMMAND_ORDER = ["cd", "ls", "vim", "mkdir", "cat", "cp", "mv", "rm", "git", "touch", "pwd", "echo", "grep", "find"]

CHANGED_FIRST = {"add", "restore", "diff"}
MAX_CHANGED = 20

BRANCH_COMMANDS = {"switch", "merge", "rebase", "branch"}
DIR_ONLY_COMMANDS = {"cd", "rmdir"}
FILE_COMMANDS = {"vim", "vi", "nano", "cat", "less", "more", "head", "tail"}


@dataclass
class Candidate:
    value: str
    meta: str = ""
    append: str = ""  # 고르면 값 뒤에 더 넣을 글자


def candidates(text, cwd, deadline=None, curated=False):
    """입력 중인 줄의 후보를 순서대로 돌려준다. 실행하지 않고 네트워크도 쓰지 않는다.

    curated 면 표에 있는 자주 쓰는 후보만 고른다. 맞는 것이 없을 때만 전체 목록에서 찾는다.
    """
    segment = _last_segment(text)
    if segment is None:
        return []
    word = _current_word(segment)
    prefix = segment[:len(segment) - len(word)]
    real, _ = parse.unwrap(prefix.split())

    if not real:
        return [Candidate(c, shell.aliases(wait=False).get(c, "")) for c in _commands(word, curated)]
    if os.path.basename(real[0]) == "git":
        found = _git(real, word, prefix, cwd, deadline, curated)
    elif os.path.basename(real[0]) == "cd" and len(real) > 1:
        return []  # cd 는 인자를 하나만 받는다
    else:
        found = _paths(real[0], word, cwd)
    return _drop_useless(found, real[1:], word)


def _drop_useless(found, typed, word):
    """이미 쓴 인자와, 지금 쓴 단어와 똑같아서 고를 이유가 없는 후보를 뺀다."""
    typed = {t.rstrip("/") for t in typed}
    return [c for c in found if c.value.rstrip("/") not in typed and (c.value != word or c.append or c.meta)]


def _last_segment(text):
    """여러 명령이 이어진 줄의 마지막 명령. 따옴표가 아직 안 닫혔으면 None."""
    if text.count('"') % 2 or text.count("'") % 2:
        return None
    return re.split(r"&&|\|\||[;|&(]", text)[-1].lstrip()


def _current_word(segment):
    return "" if not segment or segment[-1].isspace() else segment.split()[-1]


def _commands(word, curated):
    known = learn.rank(COMMAND_ORDER + learn.frequent())
    if not word:
        return known
    fixed = [c for c in [*known, *shell.aliases(wait=False)] if c.startswith(word)]
    if curated and fixed:
        return fixed
    return fixed + [c for c in state.path_commands() if c.startswith(word) and c not in fixed]


def _git(real, word, prefix, cwd, deadline, curated):
    sub, args, where = state.git_subcommand(real, cwd)
    if sub is None:
        return _git_subcommands(word, where, curated)
    first = [v for v in learn.rank([v for v, _ in gitspec.spec(sub).first], f"git {sub}") if v.startswith(word)]
    first = first if not args else []
    if word.startswith("-"):
        return _git_options(sub, word, prefix, where, deadline, first, curated)
    if _arguments_finished(sub, parse.strip_redirects(args)):
        return []
    found = _mark_risk(first, prefix, deadline, sub) + _git_arguments(sub, args, word, where)
    return sorted(found, key=lambda c: MARK in c.meta)  # 위험 표시가 붙은 것은 맨 아래로


def _arguments_finished(sub, args):
    """더 추천할 인자가 없으면 True. 이 판단이 없으면 같은 목록이 끝없이 다시 뜬다."""
    if args and args[-1] in gitspec.FREE_TEXT_OPTIONS:
        return True
    if "--" in args:
        return False  # -- 뒤는 파일 목록이다
    spec = gitspec.spec(sub)
    if any(a in args for a in spec.done_after):
        return True
    return spec.max_args is not None and len(_plain(args)) >= spec.max_args


def _git_arguments(sub, args, word, cwd):
    if "--" in args:
        return _paths("git", word, cwd)
    if sub == "fetch":
        return [Candidate(n) for n in state.remotes(cwd) if n.startswith(word)]
    if sub == "pull":
        return _pull_arguments(_plain(args), word, cwd)
    if sub == "push":
        names = state.remotes(cwd) if not _plain(args) else state.branches(cwd)
        return [Candidate(n) for n in names if n.startswith(word)]
    if sub in BRANCH_COMMANDS:
        return [Candidate(n) for n in state.branches(cwd) if n.startswith(word)]
    paths = _paths("git", word, cwd)
    if sub == "checkout":
        return [Candidate(n) for n in state.branches(cwd) if n.startswith(word)] + paths
    if sub in CHANGED_FIRST:
        changed = _changed(sub, word, cwd)
        names = {c.value for c in changed}
        return changed + [c for c in paths if c.value not in names]
    return paths


def _pull_arguments(plain, word, cwd):
    """pull 할 브랜치를 받을 커밋 수와 함께 맨 위에 모은다. 원격 이름만 쓰는 후보는 그 아래에 둔다."""
    remotes = state.remotes(cwd)
    current = state.current_branch(cwd)
    upstream = state.upstream(current, cwd) if current else None
    found = []
    for remote in plain[:1] or remotes:
        for branch, count in state.pull_counts(remote, cwd).items():
            is_upstream = upstream == (remote, branch)
            remote_state = statusline.latest(cwd) if is_upstream else None
            if remote_state:  # 상태 줄이 원격에 직접 물어본 값이 더 새롭다
                count = remote_state.behind
            value = branch if plain else f"{remote} {branch}"
            if value.startswith(word) or branch.startswith(word):
                found.append((not is_upstream, -count, value, _pull_meta(count, is_upstream)))
    branches = [Candidate(value, meta) for *_, value, meta in sorted(found)]
    return branches + ([] if plain else [Candidate(r, "원격 저장소") for r in remotes if r.startswith(word)])


def _pull_meta(count, is_upstream):
    if not count:
        return "받을 커밋 없음"
    # git pull origin main 은 main 으로 옮겨 가는 것이 아니라 지금 브랜치에 main 을 합친다
    return f"↓{count} 받을 커밋" + ("" if is_upstream else " · 지금 브랜치에 합침")


def _changed(sub, word, cwd):
    """실제로 바뀐 파일. git add 뒤에는 대개 이 파일들이 온다."""
    tracked_only = sub != "add"  # 미추적 파일은 restore, diff 의 대상이 아니다
    names = state.changed_paths(cwd, tracked_only)
    return [Candidate(shlex.quote(n), "변경됨") for n in names[:MAX_CHANGED] if shlex.quote(n).startswith(word)]


def _plain(args):
    return [a for a in parse.strip_redirects(args) if not a.startswith("-")]


def _current_snapshot(cwd):
    """상태 줄이 원격에 직접 물어본 값이 추적 정보보다 새로우므로 그 값을 쓴다. 확인하지 못했으면 쓰지 않는다."""
    snap = state.snapshot(cwd)
    remote = statusline.latest(cwd)
    if snap and remote and remote.kind in statusline.COMPARED:
        snap.ahead, snap.behind = remote.ahead, remote.behind
    return snap


def _git_subcommands(word, cwd, curated):
    snap = _current_snapshot(cwd)
    publish = _publish_command(snap, cwd)
    up, down = _state_rules(snap, publish)
    known = learn.rank([*gitspec.COMMANDS, *learn.frequent("git")], "git")
    # 표에 맞는 명령이 있으면 archimport 같이 거의 안 쓰는 명령은 자동 목록에 띄우지 않는다
    others = [] if curated and any(n.startswith(word) for n in known) else state.git_commands()
    real = _real_values(word, snap, cwd)
    meta = {**gitspec.RULE_HINTS, publish: "upstream 설정", **{c.value: c.meta for c in real}}
    # 상태 규칙의 "rebase --continue" 처럼 옵션까지 붙은 추천과 실제 값으로 채운 명령도 후보로 넣는다
    ordered = [u for u in up if " " in u] + [c.value for c in real] + known + others
    ranked = _rank([v for v in ordered if v.startswith(word)], up, down)
    return [Candidate(v, meta.get(v, gitspec.spec(v).desc), " " if gitspec.takes_space(v) else "") for v in ranked]


def _real_values(word, snap, cwd):
    """저장소에서 읽은 실제 값으로 완성한 명령. 예시와 달리 그대로 실행할 수 있다. 입력 전에는 목록을 어지럽히지 않는다."""
    if not word or snap is None:
        return []
    found = []
    publish = _publish_command(snap, cwd)
    if "push".startswith(word) and publish:
        found.append(Candidate(publish, "upstream 설정"))
    if "switch".startswith(word):
        previous = state.previous_branch(cwd)
        if previous:
            found.append(Candidate(f"switch {previous}", "직전 브랜치"))
    if "branch".startswith(word):
        found.extend(Candidate(f"branch -d {b}", "병합 완료") for b in state.merged_branches(cwd, snap.branch)[:3])
    if "commit".startswith(word) and snap.staged:
        found.append(Candidate("commit -m", "스테이지된 변경 기록"))
    if "reset".startswith(word) and state.has_parent(cwd):
        found.append(Candidate("reset HEAD~1", gitspec.hint("reset", "HEAD~1")))
    if "stash".startswith(word) and state.has_stash(cwd):
        found.append(Candidate("stash pop", gitspec.hint("stash", "pop")))
    return found


def _git_options(sub, word, prefix, cwd, deadline, first, curated):
    up, down = _state_rules(_current_snapshot(cwd))
    up = [u.removeprefix(f"{sub} ") for u in up]
    known = learn.rank([*first, *(v for v, _ in gitspec.spec(sub).options), *learn.frequent(f"git {sub}")], f"git {sub}")
    matches = [o for o in known if o.startswith(word)]
    if curated and matches:
        options = matches
    else:
        options = known + state.git_options(sub, cwd, negated=word.startswith("--no"))
    return _mark_risk(_rank([o for o in options if o.startswith(word)], up, down), prefix, deadline, sub)


def _rank(values, up, down):
    """2단계: 상태 규칙. 권하는 후보를 위로, 맞지 않는 후보를 아래로 옮긴다."""
    def key(value):
        if value in up:
            return 0, up.index(value)
        return (2, 0) if value in down else (1, 0)
    return sorted(dict.fromkeys(values), key=key)


def _publish_command(snap, cwd):
    """올린 적 없는 브랜치를 올리는 명령. 그냥 git push 는 upstream 이 없어 실패한다."""
    if snap is None or not snap.branch or snap.upstream or snap.initial:
        return None
    remotes = state.remotes(cwd)
    if not remotes:
        return None
    return f"push -u {'origin' if 'origin' in remotes else remotes[0]} {snap.branch}"


def _state_rules(snap, publish=None):
    """상태 줄의 알림을 해야 할 일의 순서로 옮긴다. (위로, 아래로) 를 돌려준다.

    순서는 작업 흐름을 따른다. 충돌 해결, 받기, add, commit, push 순이다. 근거는 각 줄의 git advice 이다.
    """
    up, down = [], []
    if snap is None:
        return up, down

    def rule(condition, raise_, lower=()):
        if condition:
            up.extend(raise_)
            down.extend(lower)

    working = not snap.conflicts and not snap.rebasing  # 충돌을 풀기 전에는 다른 일을 권하지 않는다
    rule(snap.rebasing, ["rebase --continue", "rebase --abort", "add"], ["rebase", "merge"])  # advice.resolveConflict
    rule(snap.conflicts, ["add", "mergetool"], ["commit"])  # advice.mergeConflict
    rule(snap.behind and snap.ahead, ["pull --rebase"], ["push"])  # advice.diverging
    rule(snap.behind and not snap.ahead, ["pull", "--force-with-lease"], ["push"])  # advice.pushFetchFirst
    rule(working and snap.to_add, ["add"])  # advice.statusHints
    rule(working and snap.staged, ["commit"])  # advice.statusHints
    rule(working and snap.ahead and not snap.behind, ["push"])  # advice.statusAheadBehind
    rule(working and publish, [publish])  # 올린 적 없는 브랜치
    rule(working and snap.changed, ["stash"], ["merge"])  # advice.commitBeforeMerge
    rule(snap.detached, ["switch -c"])  # advice.detachedHead
    rule(not snap.changed and not snap.untracked, [], ["add", "commit", "stash"])  # advice.statusHints
    return up, down


def _mark_risk(values, prefix, deadline, sub):
    """3단계: 위험 표시. L2 이상은 맨 아래로 보내고 표시한다. 숨기지는 않는다."""
    safe, risky = [], []
    for value in values:
        plain = Candidate(value, gitspec.hint(sub, value), " " if value in gitspec.TAKES_VALUE else "")
        if deadline is not None and time.monotonic() > deadline:
            safe.append(plain)  # 시간이 다 되면 호출한 쪽이 목록을 버린다
            continue
        verdict = gate.preview(prefix + value)
        if verdict.level >= L2:
            risky.append(Candidate(value, f"{MARK} L{verdict.level} {plain.meta}".rstrip(), plain.append))
        else:
            safe.append(plain)
    return safe + risky


def _paths(command, word, cwd):
    """파일 이름 후보. 알파벳순이고, rm 은 되살릴 수 없는 파일을 맨 아래로 보내고 표시한다."""
    head, tail = os.path.split(word)
    base = os.path.join(cwd, os.path.expanduser(head))
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError:
        return []

    name = os.path.basename(command)
    files, dirs = [], []
    for entry in entries:
        if not entry.name.startswith(tail) or (entry.name.startswith(".") and not tail.startswith(".")):
            continue
        if entry.is_dir():
            dirs.append(entry.name)
        elif name not in DIR_ONLY_COMMANDS:
            files.append(entry.name)

    def candidate(entry_name, suffix="", meta=""):
        path = os.path.join(head, entry_name)
        # ~ 를 따옴표로 감싸면 셸이 홈 폴더로 펼치지 않는다
        quoted = "~/" + shlex.quote(path[2:]) if path.startswith("~/") else shlex.quote(path)
        return Candidate(quoted + suffix, meta)

    if name == "rm":
        lost = state.unrecoverable_names(base)
        names = sorted(files + dirs)
        slash = set(dirs)
        kept = [candidate(n, "/" if n in slash else "") for n in names if n not in lost]
        return kept + [candidate(n, "/" if n in slash else "", f"{MARK} 복구 불가") for n in names if n in lost]
    if name in FILE_COMMANDS:  # 파일이 먼저. 폴더는 그 안으로 들어가려고 고르는 것이라 뒤에 둔다
        return [candidate(n) for n in files] + [candidate(n, "/") for n in dirs]
    return [candidate(n, "/" if n in set(dirs) else "") for n in sorted(files + dirs)]


class GateCompleter(Completer):
    def get_completions(self, document, complete_event):
        automatic = not complete_event.completion_requested
        text = document.text_before_cursor
        started = time.monotonic()
        deadline = started + BUDGET_SECONDS if automatic else None
        try:
            found = candidates(text, os.getcwd(), deadline, curated=automatic)
        except OSError:  # 입력 중 현재 폴더가 사라진 경우
            return
        # 자동으로 뜨는 목록은 방해가 되면 안 되므로, 너무 많거나 늦으면 띄우지 않는다
        if automatic and (len(found) > MAX_CANDIDATES or time.monotonic() - started > BUDGET_SECONDS):
            return

        word = _current_word(_last_segment(text) or "")
        for c in found:
            yield Completion(c.value + c.append, start_position=-len(word), display=c.value, display_meta=c.meta)


class IdleTrigger:
    """입력이 잠시 멈추면 후보 목록을 연다. 키를 누를 때마다 타이머를 다시 시작한다."""

    def __init__(self, idle=IDLE_SECONDS):
        self.idle = idle
        self.task = None

    def __call__(self, buffer):
        if self.task and not self.task.done():
            self.task.cancel()
        self.task = get_app().create_background_task(self._open_later(buffer))

    async def _open_later(self, buffer):
        try:
            await asyncio.sleep(self.idle)
        except asyncio.CancelledError:
            return
        state_ = buffer.complete_state
        if state_ is not None:
            # 후보가 하나뿐이면 고른 것이 확실하니 다음 후보로 넘어간다. 여럿이면 고르는 중일 수 있어 둔다
            if state_.current_completion is None or len(state_.completions) > 1 or not buffer.text.endswith(" "):
                return
            buffer.complete_state = None
        if buffer.text.strip():
            _open(buffer)


def _open(buffer):
    buffer.start_completion(select_first=False, complete_event=CompleteEvent(text_inserted=True))


def key_bindings():
    bindings = KeyBindings()

    @bindings.add(" ", filter=has_completions & completion_is_selected)
    def accept_and_continue(event):
        """목록에서 고른 뒤 스페이스를 누르면 확정하고 다음 후보로 넘어간다."""
        buffer = event.current_buffer
        buffer.complete_state = None
        if buffer.text.endswith((" ", "/")):
            _open(buffer)
        else:
            buffer.insert_text(" ")

    return bindings


def completer():
    # 판정이 입력을 막지 않도록 별도 스레드에서 돌린다
    return ThreadedCompleter(GateCompleter())
