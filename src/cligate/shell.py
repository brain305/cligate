"""명령을 실행할 사용자의 셸과, 그 셸에 정의된 별칭을 다룬다.

셸은 -c 로 실행하면 .zshrc 같은 설정 파일을 읽지 않아 별칭을 모른다. 그렇다고 명령마다 설정 파일을
읽으면 1~2초씩 걸리므로, 시작할 때 백그라운드에서 한 번만 읽어 저장해 둔다.
"""
import os
import re
import shlex
import signal
import subprocess
import threading

FALLBACK = "/bin/sh"
SUPPORTED = {"sh", "bash", "zsh", "dash", "ksh"}  # 이 셸들은 trap, eval, printf 를 같은 문법으로 쓴다. fish 등은 다르다
ALIAS_LISTING = {"zsh": "alias -L", "bash": "alias -p"}
LOAD_TIMEOUT = 5

_loader = None
_definitions = []  # 셸에 그대로 넘길 alias 줄
_aliases = {}


def path():
    """사용자의 셸 경로. 지원하지 않거나 없으면 /bin/sh."""
    shell = os.environ.get("SHELL", "")
    if os.path.basename(shell) in SUPPORTED and os.access(shell, os.X_OK):
        return shell
    return FALLBACK


def start_loading():
    """별칭 읽기를 시작한다. 시작 화면을 기다리게 하지 않으려고 백그라운드에서 돈다."""
    global _loader
    _loader = threading.Thread(target=_load, daemon=True)
    _loader.start()


def aliases(wait=True):
    """{별칭: 값}. 입력 중에는 wait=False 로 불러 기다리지 않는다. 판정 때는 아직 안 읽었으면 기다린다."""
    if wait and _loader is not None:
        _loader.join(LOAD_TIMEOUT)
    return _aliases


def script(line, save_cwd):
    """실행할 스크립트. zsh 는 한 번에 읽은 줄의 별칭을 펼치지 않으므로 eval 을 거쳐 뒤늦게 읽게 한다."""
    if not set(re.split(r"[\s;&|()]+", line)) & aliases().keys():
        # 별칭을 안 쓰면 eval 이 필요 없다. 쓰면 오류 문구 앞에 (eval):1: 이 붙는다
        return f"trap {shlex.quote(save_cwd)} EXIT; {line}"
    lines = list(_definitions)
    if lines and os.path.basename(path()) == "bash":
        lines.insert(0, "shopt -s expand_aliases")  # bash 는 -c 에서 별칭이 기본으로 꺼져 있다
    lines.append(f"trap {shlex.quote(save_cwd)} EXIT; eval {shlex.quote(line)}")
    return "\n".join(lines)


def _load():
    listing = ALIAS_LISTING.get(os.path.basename(path()))
    if listing is None:
        return
    output = _read_interactive(listing)
    definitions, found = _parse_aliases(output)
    _definitions[:] = definitions
    _aliases.update(found)


def _read_interactive(command):
    try:
        proc = subprocess.Popen([path(), "-ic", command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace", start_new_session=True)
    except OSError:
        return ""
    try:
        out, _ = proc.communicate(timeout=LOAD_TIMEOUT)
        return out
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return ""


def _parse_aliases(output):
    definitions, found = [], {}
    for line in output.splitlines():
        if not line.startswith("alias "):
            continue  # 설정 파일이 출력한 다른 글자
        try:
            words = shlex.split(line)[1:]
        except ValueError:
            continue
        marked = words[:1] == ["--"]  # alias -- -='cd -' 처럼 이름이 - 로 시작하는 별칭
        words = words[1:] if marked else words
        if len(words) != 1 or "=" not in words[0] or (words[0].startswith("-") and not marked):
            continue  # zsh 의 -g, -s 별칭은 명령 자리가 아닌 곳에서도 펼쳐져 다루지 않는다
        name, value = words[0].split("=", 1)
        definitions.append(line)
        found[name] = value
    return definitions, found
