"""정책 기록. 1단계는 observe 모드 규칙에 걸린 사건을 사용자 폴더에 남기고 규칙별로 센다.

한 줄에 사건 하나인 JSON(JSONL)이다. 여러 서버의 파일을 모아 합쳐도 id 로 중복을 걸러낸다.
명령은 규칙에 적힌 이름만 남긴다. 인자에는 비밀번호 같은 값이 섞일 수 있어 남기지 않는다.
"""
import datetime
import getpass
import gzip
import json
import os
import secrets
import socket
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import policy

REQUIRED = ("id", "time", "host", "policy", "version", "rule", "mode", "result")
PASSED, CANCELLED = "passed", "cancelled"


def observe_path():
    """개인 규칙과 프로젝트 규칙의 관찰 기록."""
    return os.path.join(policy.state_dir(), "observe.jsonl")


def server_observe_path():
    """팀 규칙의 관찰 기록. 사용자마다 파일이 따로라 남의 기록을 고칠 수 없다 (폴더 권한 1733)."""
    return os.path.join(policy.log_dir(), f"observe-{getpass.getuser()}.jsonl")


def default_files():
    """policy hits 가 파일 없이 불렸을 때 읽는 기록. 관리자는 서버의 모든 사용자 기록을 읽는다."""
    try:
        server = sorted(os.path.join(policy.log_dir(), n) for n in os.listdir(policy.log_dir())
                        if n.startswith("observe-") and n.endswith(".jsonl"))
    except OSError:
        server = []
    return [observe_path(), *server]


def record_observed(rules, cwd, result, path=None):
    """observe 로 걸린 규칙들을 기록한다. 기록에 실패해도 셸은 계속 동작해야 한다.

    팀 규칙은 서버 기록에 남긴다. 서버 기록에 쓸 수 없으면 잃지 않도록 사용자 기록에 남긴다.
    """
    if not rules:
        return
    if path is not None:
        _append(path, _lines(rules, cwd, result))
        return
    team = [r for r in rules if r.source == policy.SYSTEM]
    others = [r for r in rules if r.source != policy.SYSTEM]
    if team and not _append(server_observe_path(), _lines(team, cwd, result)):
        others = team + others
    if others:
        _append(observe_path(), _lines(others, cwd, result))


def _lines(rules, cwd, result):
    host = socket.gethostname().split(".")[0]
    now = datetime.datetime.now().astimezone()
    lines = []
    for rule in rules:
        lines.append(json.dumps({
            "id": f"{host}-{int(now.timestamp())}-{secrets.token_hex(3)}",
            "time": now.isoformat(timespec="seconds"),
            "host": host,
            "user": getpass.getuser(),
            "cwd": cwd,
            "source": rule.source,
            "policy": rule.policy,
            "version": rule.version,
            "rule": rule.id,
            "mode": rule.mode,
            "level": rule.level,
            "command": " ".join(rule.command),
            "result": result,
        }, ensure_ascii=False))
    return lines


def _append(path, lines):
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in lines))
        return True
    except OSError:
        return False


@dataclass
class Summary:
    files: int = 0
    lines: int = 0
    duplicates: int = 0
    unreadable: int = 0
    first: str = None
    last: str = None
    rows: dict = field(default_factory=dict)  # (정책, 버전, 규칙) → Row


@dataclass
class Row:
    count: int = 0
    hosts: set = field(default_factory=set)
    users: set = field(default_factory=set)
    latest: str = ""
    by: Counter = field(default_factory=Counter)


def _open(path):
    return gzip.open(path, "rt", encoding="utf-8") if path.endswith(".gz") else open(path, encoding="utf-8")


def summarize(files, since=None, rule=None, by=None):
    """기록 파일들을 합쳐 (정책, 버전, 규칙) 별로 센다. since 는 이 시각 이후만, by 는 host 나 user 로 나눠 셈."""
    summary = Summary()
    seen = set()
    rows = defaultdict(Row)
    for path in files:
        try:
            handle = _open(path)
        except OSError:
            continue
        summary.files += 1
        with handle:
            for line in handle:
                if not line.strip():
                    continue
                summary.lines += 1
                try:
                    event = json.loads(line)
                    when = datetime.datetime.fromisoformat(event["time"])
                    if when.tzinfo is None:  # 시간대가 빠진 기록은 이 컴퓨터의 시간대로 본다
                        when = when.astimezone()
                except (ValueError, KeyError, TypeError):
                    summary.unreadable += 1
                    continue
                if not isinstance(event, dict) or any(key not in event for key in REQUIRED):
                    summary.unreadable += 1
                    continue
                if event["id"] in seen:
                    summary.duplicates += 1
                    continue
                seen.add(event["id"])
                if since is not None and when < since:
                    continue
                if rule is not None and event["rule"] != rule:
                    continue
                row = rows[(event["policy"], event["version"], event["rule"])]
                row.count += 1
                row.hosts.add(event["host"])
                row.users.add(event.get("user", ""))
                row.latest = max(row.latest, event["time"])
                if by:
                    row.by[event.get(by, "")] += 1
                summary.first = min(summary.first or event["time"], event["time"])
                summary.last = max(summary.last or event["time"], event["time"])
    summary.rows = dict(rows)
    return summary


def parse_since(text, now=None):
    """7d, 24h, 30m 같은 기간을 그 시각으로 바꾼다."""
    units = {"d": "days", "h": "hours", "m": "minutes"}
    if not text or text[-1] not in units or not text[:-1].isdigit():
        raise ValueError(text)
    now = now or datetime.datetime.now().astimezone()
    return now - datetime.timedelta(**{units[text[-1]]: int(text[:-1])})
