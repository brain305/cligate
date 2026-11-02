"""최근에 실행한 명령으로 자동완성 순서를 갱신한다.

기록에는 명령 이름과 알려진 옵션 이름만 남긴다. 인자에는 커밋 메시지, 파일 이름, 토큰처럼
남기면 안 되는 값이 들어 있을 수 있어 저장하지 않는다.
"""
import os
import re
from collections import Counter

from . import APP_NAME, gitspec, parse, state

MAX_ENTRIES = 1000
PRIOR_WEIGHT = 10  # 기본 1순위가 이만큼 쓴 것과 같다. 한두 번 쓴 것으로 순서가 뒤집히지 않게 한다
MIN_USES = 3       # 표에 없는 명령은 이만큼 써야 자동 목록에 나온다
SAFE_NAME = re.compile(r"^[\w.+-]+$")

_cache = (None, None, Counter())


def history_path():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, APP_NAME, "history")


def record(commands):
    """실행한 명령을 기록한다. 기록에 실패해도 셸은 계속 동작해야 한다."""
    entries = [e for e in (_entry(argv) for argv in commands) if e]
    if not entries:
        return
    path = history_path()
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write("".join(e + "\n" for e in entries))
        _trim(path)
    except OSError:
        pass


def rank(values, scope=""):
    """기본 순서에 사용 횟수를 더해 정렬한다. 값은 앞에 있을수록 기본 점수가 높다."""
    uses = _counts()
    total = len(values)

    def score(item):
        index, value = item
        return uses[_key(scope, value)] + PRIOR_WEIGHT * (total - index) / total

    ordered = sorted(enumerate(dict.fromkeys(values)), key=score, reverse=True)
    return [value for _, value in ordered]


def frequent(scope=""):
    """scope 바로 아래에서 자주 쓴 이름들. 표에 없는 명령도 자주 쓰면 자동 목록에 나오게 한다."""
    depth = len(scope.split()) + 1
    prefix = f"{scope} " if scope else ""
    return [key.split()[-1] for key, count in _counts().items()
            if count >= MIN_USES and key.startswith(prefix) and len(key.split()) == depth]


def _key(scope, value):
    return f"{scope} {value}" if scope else value


def _entry(argv):
    """한 명령에서 남겨도 되는 이름만 꺼낸다. 예: git push --force"""
    real, _ = parse.unwrap(argv)
    if not real:
        return None
    name = os.path.basename(real[0])
    if not SAFE_NAME.match(name):
        return None
    if name != "git":
        return name
    sub, args, _ = state.git_subcommand(real, os.getcwd())
    if sub is None or not SAFE_NAME.match(sub):
        return None
    return " ".join(["git", sub, *dict.fromkeys(o for o in map(lambda a: _safe_option(sub, a), args) if o)])


def _safe_option(sub, word):
    spec = gitspec.spec(sub)
    if word in {v for v, _ in (*spec.first, *spec.options)}:
        return word
    # --name=값 의 값과 -m메시지 처럼 붙여 쓴 값은 남기지 않는다
    if word.startswith("--"):
        name = word.split("=", 1)[0]
        return name if SAFE_NAME.match(name) else None
    return None


def _counts():
    global _cache
    path = history_path()
    try:
        info = os.stat(path)
    except OSError:
        return Counter()
    version = (info.st_mtime_ns, info.st_size)
    if _cache[0] == path and _cache[1] == version:
        return _cache[2]
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()[-MAX_ENTRIES:]
    except (OSError, UnicodeDecodeError):
        return Counter()
    counts = Counter()
    for line in lines:
        words = line.split()
        if words:
            counts[words[0]] += 1
        if len(words) >= 2:
            counts[" ".join(words[:2])] += 1
        for option in words[2:]:
            counts[" ".join([*words[:2], option])] += 1
    _cache = (path, version, counts)
    return counts


def _trim(path):
    """파일이 조금 넘치면 최근 기록만 남긴다. 읽을 때도 최근 것만 보므로 매번 자를 필요는 없다."""
    if os.path.getsize(path) < MAX_ENTRIES * 40:
        return
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    if len(lines) <= MAX_ENTRIES:
        return
    temp = f"{path}.{os.getpid()}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(lines[-MAX_ENTRIES:])
    os.replace(temp, path)
