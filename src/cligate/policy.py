"""팀 규칙(정책). 설계는 docs/policy.md 에 있고, 여기는 1단계인 개인 규칙과 프로젝트 규칙이다.

규칙은 등급을 올리기만 한다. 규칙 파일이 잘못되면 그 파일만 무시하고 기본 판정으로 동작한다.
규칙은 데이터일 뿐이어서 규칙 파일로 명령을 실행할 수 없다.
"""
import datetime
import fnmatch
import getpass
import hashlib
import json
import os
import re
import socket
import sys
from dataclasses import dataclass, field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from . import APP_NAME, gitpush, paths, state, statusline
from .risk import DEFAULT_WORD, Finding

SYSTEM, PROJECT, USER = "system", "project", "user"
SOURCE_LABEL = {SYSTEM: "팀 규칙", PROJECT: "프로젝트 규칙", USER: "개인 규칙"}
PROJECT_FILE = os.path.join(".cligate", "policy.toml")
# 시스템 정책과 서버 기록의 위치. 시험에서만 바꾼다. 바꾸면 그 사용자의 파일도 믿는데,
# 사용자가 CliGate 를 쓰지 않는 것과 같은 수준이라 막지 않는다 (docs/policy.md 13절)
ETC_ENV, LOG_ENV = "CLIGATE_ETC_DIR", "CLIGATE_LOG_DIR"
ENFORCE, OBSERVE = "enforce", "observe"

TOP_KEYS = {"policy", "audit", "rule"}
POLICY_KEYS = {"name", "version"}
AUDIT_KEYS = {"min_level", "policy_hits", "full_command"}  # 3단계의 감사 기록에서 쓴다. 지금은 형식만 검사한다
RULE_KEYS = {"id", "command", "level", "message", "args", "path", "when", "word", "suggest", "owner", "mode",
             "expires", "tests"}
REQUIRED = ("id", "command", "level", "message")
TEXT_CONDITIONS = {"branch", "remote", "host", "cwd", "user", "group"}
FLAG_CONDITIONS = {"forced", "remote_ahead", "untracked"}
SIZE_CONDITIONS = {"count", "bytes"}
ID = re.compile(r"^[\w.-]+$")
MAX_COUNTED = 10000  # 파일 수를 세다 이만큼 넘으면 멈춘다. 조건은 "이 수 이상" 이라 정확한 값이 필요 없다

_cache = {}


@dataclass
class Rule:
    id: str
    command: tuple
    level: int
    message: str
    args: tuple = ()
    path: tuple = ()
    when: dict = field(default_factory=dict)
    word: str = None
    suggest: str = None
    owner: str = None
    mode: str = OBSERVE
    expires: datetime.date = None
    tests: tuple = ()
    source: str = USER
    policy: str = ""
    version: int = 0

    @property
    def label(self):
        return f"{SOURCE_LABEL[self.source]} {self.id}"

    def expired(self, today=None):
        return self.expires is not None and self.expires < (today or datetime.date.today())


@dataclass
class Policy:
    file: str
    source: str
    name: str = ""
    version: int = 0
    rules: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    trusted: bool = True
    digest: str = ""

    @property
    def usable(self):
        return not self.errors and self.trusted


def user_file():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_NAME, "policy.toml")


def state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, APP_NAME)


def etc_dir():
    return os.environ.get(ETC_ENV) or os.path.join("/etc", APP_NAME)


def system_dir():
    return os.path.join(etc_dir(), "policy.d")


def log_dir():
    return os.environ.get(LOG_ENV) or os.path.join("/var/log", APP_NAME)


def system_files():
    try:
        names = sorted(n for n in os.listdir(system_dir()) if n.endswith(".toml"))
    except OSError:
        return []
    return [os.path.join(system_dir(), n) for n in names]


def unsafe_reason(path):
    """시스템 파일과 그 위 폴더가 root 소유이고 다른 사람이 쓸 수 없으면 None, 아니면 이유.

    sudo 가 sudoers 를 확인하는 것과 같은 방식이다. 다른 사람이 바꿀 수 있는 정책은 쓰지 않는다.
    """
    owners = {0} | ({os.getuid()} if os.environ.get(ETC_ENV) else set())
    top = os.path.abspath(etc_dir())
    current = os.path.abspath(path)
    try:
        if os.path.islink(current):
            return f"링크입니다: {current}"
        while True:
            info = os.stat(current)
            if info.st_uid not in owners:
                return f"root 소유가 아닙니다: {current}"
            if info.st_mode & 0o022:
                return f"다른 사람이 쓸 수 있습니다: {current}"
            if current == top or os.path.dirname(current) == current:
                return None
            current = os.path.dirname(current)
    except OSError as e:
        return f"확인하지 못했습니다: {e.strerror}"


def project_file(cwd):
    """cwd 와 그 위 폴더에서 가장 가까운 .cligate/policy.toml. 없으면 None."""
    folder = os.path.abspath(cwd)
    while True:
        candidate = os.path.join(folder, PROJECT_FILE)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def load(path, source, today=None, strict=False):
    """규칙 파일 하나를 읽어 Policy 로 돌려준다. 읽지 못하거나 틀린 곳이 있으면 errors 에 담는다.

    strict 는 policy check 용이다. 기한이 지난 규칙도 오류로 알린다. 평소에는 그 규칙만 꺼진다.
    """
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        return Policy(path, source, errors=[f"읽지 못했습니다: {e.strerror}"])
    policy = Policy(path, source, digest=hashlib.sha256(raw).hexdigest())
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        policy.errors.append(f"TOML 형식이 아닙니다: {e}")
        return policy
    policy.name, policy.version, policy.rules, policy.errors = validate(data, source, path, today, strict)
    return policy


def validate(data, source, path, today=None, strict=False):
    """(이름, 버전, 규칙들, 오류들). 오류가 하나라도 있으면 파일 전체를 쓰지 않는다."""
    errors = []
    for key in data.keys() - TOP_KEYS:
        errors.append(f"모르는 항목입니다: {key}")
    head = data.get("policy", {})
    if not isinstance(head, dict):
        head = {}
        errors.append("[policy] 는 표여야 합니다")
    for key in head.keys() - POLICY_KEYS:
        errors.append(f"[policy] 에 모르는 항목입니다: {key}")
    name = head.get("name", _default_name(path, source))
    version = head.get("version", 0)
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        errors.append("[policy] version 은 0 이상의 정수여야 합니다")
        version = 0
    audit = data.get("audit", {})
    for key in (audit.keys() - AUDIT_KEYS if isinstance(audit, dict) else ["(표가 아님)"]):
        errors.append(f"[audit] 에 모르는 항목입니다: {key}")

    rules = []
    seen = set()
    entries = data.get("rule", [])
    if not isinstance(entries, list):
        entries = []
        errors.append("규칙은 [[rule]] 로 적어야 합니다")
    for number, entry in enumerate(entries, 1):
        rule, problems = _rule(entry, source, name, version, today, strict)
        where = f"규칙 {number}" + (f" ({entry.get('id')})" if isinstance(entry, dict) and entry.get("id") else "")
        errors.extend(f"{where}: {p}" for p in problems)
        if rule is None:
            continue
        if rule.id in seen:
            errors.append(f"{where}: id 가 겹칩니다")
        seen.add(rule.id)
        rules.append(rule)
    return name, version, rules, errors


def _default_name(path, source):
    """이름이 없으면 팀 규칙은 파일 이름, 개인 규칙은 personal, 프로젝트 규칙은 저장소 폴더 이름을 쓴다."""
    if source == SYSTEM:
        return os.path.splitext(os.path.basename(path))[0]
    if source == PROJECT:
        return os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(path)))) or "project"
    return "personal"


def _rule(entry, source, policy_name, version, today, strict):
    if not isinstance(entry, dict):
        return None, ["표여야 합니다"]
    problems = [f"모르는 필드입니다: {key}" for key in sorted(entry.keys() - RULE_KEYS)]
    problems += [f"{key} 가 없습니다" for key in REQUIRED if key not in entry]
    if any(key not in entry for key in REQUIRED):
        return None, problems

    rule_id, command, level, message = (entry[key] for key in REQUIRED)
    if not isinstance(rule_id, str) or not ID.match(rule_id):
        problems.append("id 는 영문, 숫자, - _ . 만 쓸 수 있습니다")
    if not isinstance(command, str) or not command.split():
        problems.append("command 가 비어 있습니다")
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 3:
        problems.append("level 은 1~3 입니다. 규칙은 등급을 올리기만 할 수 있습니다")
    if not isinstance(message, str) or not message.strip():
        problems.append("message 가 비어 있습니다")
    args = _texts(entry, "args", problems)
    path = _texts(entry, "path", problems)
    when = _when(entry.get("when", {}), problems)
    word = _text(entry, "word", problems)
    if word is not None and (not word or any(c.isspace() for c in word)):
        problems.append("word 는 띄어쓰기 없는 한 단어여야 합니다")
    mode = entry.get("mode", OBSERVE)
    if mode not in (ENFORCE, OBSERVE):
        problems.append("mode 는 enforce 또는 observe 입니다")
    expires = _date(entry.get("expires"), problems)
    if strict and expires is not None and expires < (today or datetime.date.today()):
        problems.append(f"expires 가 이미 지났습니다 ({expires})")
    tests = _tests(entry.get("tests", []), problems)
    for pattern in args + path + tuple(v for values in when.values() if isinstance(values, tuple) for v in values):
        if pattern.count("[") != pattern.count("]"):
            problems.append(f"와일드카드의 [ ] 짝이 맞지 않습니다: {pattern}")
    if problems:
        return None, problems
    return Rule(rule_id, tuple(command.split()), level, message.strip(), args, path, when, word,
                _text(entry, "suggest", problems), _text(entry, "owner", problems), mode, expires, tests,
                source, policy_name, version), problems


def _text(entry, key, problems):
    value = entry.get(key)
    if value is not None and not isinstance(value, str):
        problems.append(f"{key} 는 문자열이어야 합니다")
        return None
    return value


def _texts(entry, key, problems):
    value = entry.get(key, [])
    value = [value] if isinstance(value, str) else value
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        problems.append(f"{key} 는 문자열 목록이어야 합니다")
        return ()
    return tuple(value)


def _when(when, problems):
    if not isinstance(when, dict):
        problems.append("when 은 표여야 합니다")
        return {}
    conditions = {}
    for key, value in when.items():
        if key in TEXT_CONDITIONS:
            conditions[key] = _texts(when, key, problems)
        elif key in FLAG_CONDITIONS:
            if isinstance(value, bool):
                conditions[key] = value
            else:
                problems.append(f"when.{key} 는 true 또는 false 입니다")
        elif key in SIZE_CONDITIONS:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                conditions[key] = value
            else:
                problems.append(f"when.{key} 는 0 이상의 정수입니다")
        else:
            problems.append(f"모르는 조건입니다: when.{key}")
    return conditions


def _date(value, problems):
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except ValueError:
        problems.append("expires 는 2026-12-31 같은 날짜여야 합니다")
        return None


def _tests(tests, problems):
    if not isinstance(tests, list):
        problems.append("tests 는 목록이어야 합니다")
        return ()
    found = []
    for test in tests:
        expect = test.get("expect") if isinstance(test, dict) else None
        if (not isinstance(test, dict) or not isinstance(test.get("command"), str)
                or not isinstance(expect, int) or isinstance(expect, bool) or not 0 <= expect <= 3):
            problems.append("tests 의 항목은 { command = \"...\", expect = 0~3 } 입니다")
            continue
        found.append((test["command"], expect))
    return tuple(found)


# ── 적용 ──────────────────────────────────────────────────────────────

def policies(cwd):
    """지금 폴더에 적용되는 정책들. 우선순위가 높은 것(팀, 프로젝트, 개인)부터. 쓰지 않는 것도 담는다."""
    found = []
    for path in system_files():
        reason = unsafe_reason(path)
        found.append(Policy(path, SYSTEM, errors=[f"권한이 안전하지 않아 쓰지 않습니다. {reason}"])
                     if reason else _cached(path, SYSTEM))
    for path, source in ((project_file(cwd), PROJECT), (user_file(), USER)):
        if path and os.path.isfile(path):
            found.append(_cached(path, source))
    return found


def _cached(path, source):
    try:
        info = os.stat(path)
    except OSError:
        return Policy(path, source, errors=["읽지 못했습니다"])
    key = (path, info.st_mtime_ns, info.st_size, datetime.date.today())
    policy = _cache.get(key)
    if policy is None:
        policy = load(path, source)
        if len(_cache) > 32:  # 파일이 바뀔 때마다 새 항목이 생기므로 가끔 비운다
            _cache.clear()
        _cache[key] = policy
    if source == PROJECT:
        policy.trusted = is_trusted(path, policy.digest)
    return policy


def active_rules(cwd):
    return [rule for policy in policies(cwd) if policy.usable for rule in policy.rules if not rule.expired()]


def evaluate(real, cwd, rules=None):
    """명령 하나에 걸리는 규칙. (enforce 판정들, observe 로 걸린 규칙들) 을 돌려준다."""
    findings, observed = [], []
    facts = Facts(real, cwd)
    for rule in active_rules(cwd) if rules is None else rules:
        if not matches(rule, real, cwd, facts):
            continue
        if rule.mode == OBSERVE:
            observed.append(rule)
        else:
            findings.append(finding(rule))
    return findings, observed


def finding(rule):
    message = f"{rule.label}: {rule.message}"
    if rule.owner:
        message += f"\n    담당  {rule.owner}"
    if rule.suggest:
        note = "  (저장소 규칙의 제안)" if rule.source == PROJECT else ""
        message += f"\n    권장  {rule.suggest}{note}"
    return Finding(rule.level, message, rule.word or DEFAULT_WORD, rule)


def matches(rule, real, cwd, facts=None):
    facts = facts or Facts(real, cwd)
    if os.path.basename(real[0]) != rule.command[0]:
        return False
    if facts.positional[:len(rule.command) - 1] != list(rule.command[1:]):
        return False
    if rule.args and not any(_sequence(real[1:], pattern.split()) for pattern in rule.args):
        return False
    if rule.path and not _any_path(facts.targets(len(rule.command) - 1), rule.path):
        return False
    return all(_condition(key, value, facts) for key, value in rule.when.items())


def _sequence(words, pattern):
    return any(all(fnmatch.fnmatchcase(words[i + j], part) for j, part in enumerate(pattern))
               for i in range(len(words) - len(pattern) + 1))


def _any_path(targets, patterns):
    patterns = [os.path.expanduser(p) for p in patterns]
    # /data/** 가 /data 자체도 가리키게 끝에 / 를 붙여서도 비교한다
    return any(fnmatch.fnmatchcase(t, p) or fnmatch.fnmatchcase(t + "/", p) for t in targets for p in patterns)


def _condition(key, value, facts):
    if key in TEXT_CONDITIONS:
        return any(fnmatch.fnmatchcase(actual, pattern) for actual in facts.get(key) for pattern in value)
    if key in FLAG_CONDITIONS:
        return facts.get(key) == value
    return facts.get(key) >= value


class Facts:
    """조건에 쓰는 상태. git 을 부르는 값은 규칙이 쓸 때만 한 번 조사한다."""

    def __init__(self, real, cwd):
        self.real = real
        self.cwd = cwd
        self.git = os.path.basename(real[0]) == "git"
        if self.git:
            sub, self.args, self.where = state.git_subcommand(real, cwd)
            self.sub = sub
            self.positional = ([sub] if sub else []) + [a for a in self.args if not a.startswith("-")]
        else:
            self.sub, self.args, self.where = None, real[1:], cwd
            self.positional = [a for a in real[1:] if not a.startswith("-")]
        self._values = {}

    def get(self, key):
        if key not in self._values:
            self._values[key] = getattr(self, f"_{key}")()
        return self._values[key]

    def targets(self, skip):
        return [paths.resolve(os.path.expanduser(w), self.cwd) for w in self.positional[skip:]] + \
            paths.expand(self.positional[skip:], self.cwd)

    def _push(self):
        return self.git and self.sub == "push"

    def _branch(self):
        current = state.current_branch(self.where)
        if self._push():
            _, _, positional = gitpush._read_push(self.args)
            specs = [s.lstrip("+") for s in positional[1:]]
            if specs:
                names = [s.partition(":")[2] or s.partition(":")[0] for s in specs]
                return [current if n == "HEAD" else n for n in names if n]
        return [current] if current else []

    def _remote(self):
        if self._push():
            _, _, positional = gitpush._read_push(self.args)
            if positional:
                return [positional[0]]
        branch = state.current_branch(self.where)
        if not branch:
            return []
        if self._push():
            return [state.push_remote(branch, self.where)]
        tracked = state.upstream(branch, self.where)
        return [tracked[0]] if tracked else []

    def _forced(self):
        if not self._push():
            return False
        forced, _, positional = gitpush._read_push(self.args)
        lease = any(a.startswith(("--force-with-lease", "--force-if-includes")) for a in self.args)
        return forced or lease or any(p.startswith("+") for p in positional[1:])

    def _remote_ahead(self):
        status = statusline.latest(self.where)
        return bool(status and status.behind)

    def _files(self):
        found, total = [], 0
        for path in paths.expand(self.positional, self.cwd):
            if os.path.isdir(path) and not os.path.islink(path):
                for folder, _, names in os.walk(path):
                    found.extend(os.path.join(folder, n) for n in names)
                    if len(found) >= MAX_COUNTED:
                        break
            else:
                found.append(path)
            if len(found) >= MAX_COUNTED:
                break
        for path in found:
            try:
                total += os.lstat(path).st_size
            except OSError:
                pass
        return found, total

    def _count(self):
        return len(self._files()[0])

    def _bytes(self):
        return self._files()[1]

    def _untracked(self):
        return any(state.inspect(path)[1] for path in paths.expand(self.positional, self.cwd)[:50])

    def _host(self):
        name = socket.gethostname()
        return [name, name.split(".")[0]]

    def _cwd(self):
        return [self.cwd, self.cwd + "/"]

    def _user(self):
        return [getpass.getuser()]

    def _group(self):
        import grp
        names = []
        for gid in os.getgroups():
            try:
                names.append(grp.getgrgid(gid).gr_name)
            except KeyError:
                pass
        return names


# ── 프로젝트 규칙 승인 ──────────────────────────────────────────────

def trust_path():
    return os.path.join(state_dir(), "trusted.json")


def _trusted():
    try:
        with open(trust_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def is_trusted(path, digest):
    return bool(digest) and _trusted().get(os.path.realpath(path)) == digest


def trust(path, digest):
    """이 내용의 프로젝트 규칙을 승인한다. 파일이 바뀌면 다시 승인해야 한다."""
    data = _trusted()
    data[os.path.realpath(path)] = digest
    os.makedirs(state_dir(), mode=0o700, exist_ok=True)
    temp = f"{trust_path()}.{os.getpid()}"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(temp, trust_path())
