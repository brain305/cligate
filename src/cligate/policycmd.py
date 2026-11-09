"""cligate policy 하위 명령. 개인 규칙의 추가와 삭제, 규칙 파일의 검사와 시험, 판정 설명, 관찰 기록 집계."""
import argparse
import difflib
import json
import os
import re
import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from prompt_toolkit.utils import get_cwidth

from . import APP_NAME, audit, gate, parse, policy, shell, sync
from .policy import ENFORCE, OBSERVE, PROJECT, USER

NEW_FILE_HEAD = '[policy]\nname = "개인 규칙"\nversion = 1\n'


def main(argv):
    parser = argparse.ArgumentParser(prog=f"{APP_NAME} policy", description="팀 규칙(정책) 관리")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="개인 규칙 추가")
    add.add_argument("--command", required=True, dest="rule_command")
    add.add_argument("--level", required=True, type=int, choices=(1, 2, 3))
    add.add_argument("--message", required=True)
    add.add_argument("--args", action="append", default=[])
    add.add_argument("--path", action="append", default=[])
    for name in ("branch", "host", "cwd"):
        add.add_argument(f"--{name}", action="append", default=[])
    add.add_argument("--word")
    add.add_argument("--suggest")
    add.add_argument("--expires")
    add.add_argument("--id", dest="rule_id")
    add.add_argument("--observe", action="store_true")

    remove = sub.add_parser("remove", help="개인 규칙 삭제")
    remove.add_argument("rule_id")
    remove.add_argument("--yes", action="store_true")

    show = sub.add_parser("show", help="지금 폴더에 적용되는 규칙")
    show.add_argument("--previous", action="store_true", help="sync 가 보관한 직전 버전")
    sub.add_parser("sync", help="사내 정책 저장소에서 팀 규칙을 받아 반영 (root)")
    check = sub.add_parser("check", help="규칙 파일 검사")
    check.add_argument("files", nargs="+")
    test = sub.add_parser("test", help="규칙의 tests 실행")
    test.add_argument("file")
    explain = sub.add_parser("explain", help="명령에 걸리는 규칙과 최종 등급")
    explain.add_argument("line")
    hits = sub.add_parser("hits", help="관찰 모드 규칙이 걸린 횟수")
    hits.add_argument("files", nargs="*")
    hits.add_argument("--since")
    hits.add_argument("--rule")
    group = hits.add_mutually_exclusive_group()
    group.add_argument("--by-host", action="store_const", const="host", dest="by")
    group.add_argument("--by-user", action="store_const", const="user", dest="by")
    trust = sub.add_parser("trust", help="이 저장소의 프로젝트 규칙 승인")
    trust.add_argument("--yes", action="store_true")

    args = parser.parse_args(argv)
    return COMMANDS[args.command](args)


def _ask(question, yes):
    if yes:
        return True
    try:
        return input(f"{question} (y/N) ").strip().lower() == "y"
    except EOFError:
        return False


def _short(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def _level(rule):
    return f"L{rule.level}"


# ── check, test, explain, show ───────────────────────────────────────

def cmd_check(args):
    failed = False
    for path in args.files:
        loaded = policy.load(path, USER, strict=True)
        installed = os.path.join(policy.system_dir(), os.path.basename(path))
        if os.path.exists(installed) and os.path.abspath(installed) != os.path.abspath(path):
            current = policy.load(installed, policy.SYSTEM)
            if current.digest != loaded.digest and loaded.version <= current.version:
                loaded.errors.append(f"version 이 이 서버에 적용 중인 v{current.version} 보다 커야 합니다")
        if loaded.errors:
            failed = True
            print(f"{path}: 규칙 {len(loaded.rules)}개, 오류 {len(loaded.errors)}")
            for error in loaded.errors:
                print(f"  {error}")
        else:
            print(f"{path}: 규칙 {len(loaded.rules)}개, 오류 0")
    return 1 if failed else 0


def cmd_test(args):
    loaded = policy.load(args.file, USER)
    if loaded.errors:
        print(f"{args.file}: 오류가 있어 시험하지 못했습니다. policy check 로 확인하세요")
        return 1
    failed = 0
    cwd = os.getcwd()
    for rule in loaded.rules:
        if not rule.tests:
            continue
        passed = 0
        for line, expect in rule.tests:
            got = rule.level if _rule_hits(rule, line, cwd) else 0
            if got == expect:
                passed += 1
            else:
                failed += 1
                print(f"  실패  {rule.id}  {line}  기대 {expect}  결과 {got}")
        print(f"{rule.id}  {passed}/{len(rule.tests)} 통과")
    return 1 if failed else 0


def _rule_hits(rule, line, cwd):
    try:
        commands = parse.parse(line)
    except ValueError:
        return False
    return any(policy.matches(rule, real, cwd) for real, _ in gate._walk(commands, shell.aliases(wait=False))
               if not real[0].startswith("$"))


def cmd_explain(args):
    try:
        commands = parse.parse(args.line)
    except ValueError:
        print("명령을 해석하지 못했습니다. 따옴표가 닫혔는지 확인하세요")
        return 1
    verdict = gate.check(commands)
    base = [f for f in verdict.findings if f.rule is None]
    rows = [("기본 판정", f"L{max((f.level for f in base), default=0)}",
             base[0].message.split("\n")[0] if base else "알림 없음")]
    rows += [(f.rule.id, _level(f.rule), f.rule.message) for f in verdict.findings if f.rule is not None]
    rows += [(r.id, _level(r), f"{r.message} (관찰 모드라 지금은 기록만)") for r in verdict.observed]
    rows.append(("최종", f"L{verdict.level}", ""))
    width = max(get_cwidth(name) for name, _, _ in rows) + 2
    for name, level, text in rows:
        print(f"{_pad(name, width)}{level}  {text}".rstrip())
    return 0


def _pad(text, width):
    """한글은 두 칸을 차지하므로 글자 수가 아니라 화면 폭으로 맞춘다."""
    return text + " " * max(0, width - get_cwidth(text))


def cmd_show(args):
    if args.previous:
        kept = sync.previous_versions()
        if not kept:
            print("보관한 직전 버전이 없습니다")
        for name, loaded in kept:
            print(f"{name}  v{loaded.version}  규칙 {len(loaded.rules)}개")
            for rule in loaded.rules:
                print(f"  {rule.id}  {rule.mode}  {_level(rule)}  {' '.join(rule.command)}")
        return 0
    found = policy.policies(os.getcwd())
    if not found:
        print(f"적용 중인 규칙이 없습니다. 개인 규칙은 {_short(policy.user_file())} 에 둡니다")
        return 0
    for loaded in found:
        if loaded.errors:
            more = f" 외 {len(loaded.errors) - 1}개. policy check 로 확인하세요" if len(loaded.errors) > 1 else ""
            status = f"쓰지 않음: {loaded.errors[0]}{more}"
        elif not loaded.trusted:
            status = "승인 전이라 쓰지 않음. policy trust 로 승인하세요"
        else:
            status = "적용 중"
        print(f"{policy.SOURCE_LABEL[loaded.source]}  {_short(loaded.file)}  v{loaded.version}  {status}")
        for rule in loaded.rules:
            note = "  (기한 지남)" if rule.expired() else ""
            print(f"  {rule.id}  {rule.mode}  {_level(rule)}  {' '.join(rule.command)}{note}")
    return 0


# ── add, remove ──────────────────────────────────────────────────────

def _quote(value):
    return json.dumps(value, ensure_ascii=False)  # JSON 문자열과 목록은 그대로 TOML 이다


def _rule_text(fields):
    lines = ["", "[[rule]]"]
    for key in ("id", "command", "level", "message", "args", "path", "when", "word", "suggest", "mode", "expires"):
        value = fields.get(key)
        if value in (None, [], {}):
            continue
        if key == "when":
            inner = ", ".join(f"{k} = {_quote(v)}" for k, v in value.items())
            lines.append(f"when = {{ {inner} }}")
        elif key == "level":
            lines.append(f"level = {value}")
        else:
            lines.append(f"{key} = {_quote(value)}")
    return "\n".join(lines) + "\n"


def _next_id(command, existing):
    base = "-".join(command.split()[:2]).lower()
    base = "".join(c if c.isalnum() or c in "-_." else "-" for c in base) or "rule"
    number = 1
    while f"{base}-{number}" in existing:
        number += 1
    return f"{base}-{number}"


def cmd_add(args):
    path = policy.user_file()
    try:
        text = open(path, encoding="utf-8").read()
    except FileNotFoundError:
        text = NEW_FILE_HEAD
    except OSError as e:
        print(f"{_short(path)} 를 읽지 못했습니다: {e.strerror}")
        return 1
    current = policy.load(path, USER) if os.path.exists(path) else None
    if current and current.errors:
        print(f"{_short(path)} 에 오류가 있어 추가하지 않았습니다. policy check 로 확인하세요")
        return 1
    existing = {r.id for r in current.rules} if current else set()
    when = {name: getattr(args, name) for name in ("branch", "host", "cwd") if getattr(args, name)}
    fields = {
        "id": args.rule_id or _next_id(args.rule_command, existing),
        "command": " ".join(args.rule_command.split()),
        "level": args.level,
        "message": args.message,
        "args": args.args,
        "path": args.path,
        "when": when,
        "word": args.word,
        "suggest": args.suggest,
        "mode": OBSERVE if args.observe else ENFORCE,
        "expires": args.expires,
    }
    new_text = text.rstrip("\n") + "\n" + _rule_text(fields)
    try:
        data = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        print(f"규칙을 만들지 못했습니다: {e}")
        return 1
    _, _, rules, errors = policy.validate(data, USER, path)
    if errors:
        print("추가하지 않았습니다")
        for error in errors:
            print(f"  {error}")
        return 1
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    rule = next(r for r in rules if r.id == fields["id"])
    print(f"추가함  {_short(path)}  {rule.id}  ({rule.mode})")
    example = " ".join([*rule.command, *(a for a in (rule.args[:1]) if not any(c in a for c in "*?["))])
    if _rule_hits(rule, example, os.getcwd()):
        print(f"예      {example}  →  {_level(rule)}  {rule.message}")
    return 0


TABLE_HEAD = re.compile(r"^\s*\[\[?\s*[\w.\-]+\s*\]\]?\s*(#.*)?$")


def _blocks(text):
    """[[rule]] 블록마다 (시작 줄, 끝 줄) 을 돌려준다. 다음 표 머리글이나 파일 끝에서 끝난다."""
    lines = text.splitlines(keepends=True)
    heads = [i for i, line in enumerate(lines) if TABLE_HEAD.match(line)]
    starts = [i for i in heads if lines[i].split("#")[0].strip().replace(" ", "") == "[[rule]]"]
    blocks = []
    for start in starts:
        end = next((h for h in heads if h > start), len(lines))
        if end < len(lines):
            # 다음 머리글 바로 앞의 주석과 빈 줄은 다음 규칙의 설명이므로 남긴다
            while end > start + 1 and lines[end - 1].strip()[:1] in ("", "#"):
                end -= 1
        blocks.append((start, end))
    return lines, blocks


def cmd_remove(args):
    path = policy.user_file()
    current = policy.load(path, USER) if os.path.exists(path) else None
    ids = {r.id: r for r in current.rules} if current and not current.errors else {}
    if args.rule_id not in ids:
        for other in policy.policies(os.getcwd()):
            if other.source != USER and any(r.id == args.rule_id for r in other.rules):
                print(f"{policy.SOURCE_LABEL[other.source]}입니다. {_short(other.file)} 에서 지우세요")
                return 1
        if current and current.errors:
            print(f"{_short(path)} 에 오류가 있습니다. 파일을 직접 편집하세요")
            return 1
        close = difflib.get_close_matches(args.rule_id, list(ids), n=3)
        print(f"개인 규칙에 {args.rule_id} 가 없습니다" + (f". 비슷한 것: {', '.join(close)}" if close else ""))
        return 1

    text = open(path, encoding="utf-8").read()
    lines, blocks = _blocks(text)
    target = []
    for start, end in blocks:
        try:
            block = tomllib.loads("".join(lines[start:end]))
        except tomllib.TOMLDecodeError:
            continue
        if block.get("rule", [{}])[0].get("id") == args.rule_id:
            target.append((start, end))
    new_text = None
    if len(target) == 1:
        start, end = target[0]
        new_text = "".join(lines[:start] + lines[end:])
        try:
            before = tomllib.loads(text).get("rule", [])
            after = tomllib.loads(new_text).get("rule", [])
            if after != [r for r in before if r.get("id") != args.rule_id]:
                new_text = None
        except tomllib.TOMLDecodeError:
            new_text = None
    if new_text is None:
        print(f"규칙의 경계를 정확히 찾지 못해 지우지 않았습니다. {_short(path)} 를 직접 편집하세요")
        return 1

    rule = ids[args.rule_id]
    target_text = " ".join([*rule.command, *rule.args])
    print(f"{rule.id}  {target_text}  {_level(rule)}  {rule.message}")
    if not _ask("이 규칙을 지우면 이 명령은 다시 기본 판정을 따릅니다. 지울까요?", args.yes):
        print("지우지 않았습니다")
        return 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    print(f"지움  {_short(path)}  {rule.id}")
    return 0


# ── hits, trust ──────────────────────────────────────────────────────

def cmd_hits(args):
    try:
        since = audit.parse_since(args.since) if args.since else None
    except ValueError:
        print("--since 는 7d, 24h, 30m 처럼 씁니다")
        return 1
    files = args.files or audit.default_files()
    summary = audit.summarize(files, since=since, rule=args.rule, by=args.by)
    period = f"{summary.first[5:10]} ~ {summary.last[5:10]}" if summary.first else "기록 없음"
    print(f"기간 {period}   파일 {summary.files}개   기록 {summary.lines:,}줄   "
          f"중복 {summary.duplicates}   읽지 못한 줄 {summary.unreadable}")
    print()

    rows = dict(summary.rows)
    if not args.files:  # 지금 폴더의 관찰 규칙 중 한 번도 안 걸린 것도 보여준다
        for rule in policy.active_rules(os.getcwd()):
            if rule.mode == OBSERVE and (args.rule is None or rule.id == args.rule):
                rows.setdefault((rule.policy, rule.version, rule.id), audit.Row())
    table = [("규칙", "버전", "걸린 횟수", "서버", "사용자", "최근")]
    for (name, version, rule_id), row in sorted(rows.items(), key=lambda item: (-item[1].count, item[0][2])):
        latest = row.latest[5:16].replace("T", " ") if row.latest else "-"
        table.append((f"{name}/{rule_id}", str(version), str(row.count),
                      str(len(row.hosts)) if row.count else "-", str(len(row.users)) if row.count else "-", latest))
    _print_table(table)
    if args.by:
        for (name, _, rule_id), row in sorted(rows.items()):
            if row.by:
                print()
                _print_table([("서버" if args.by == "host" else "사용자", f"{rule_id} 걸린 횟수")]
                             + [(key, str(count)) for key, count in row.by.most_common()])
    return 0


def _print_table(rows):
    widths = [max(get_cwidth(row[i]) for row in rows) for i in range(len(rows[0]))]
    for row in rows:
        print("   ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)).rstrip())


def cmd_trust(args):
    path = policy.project_file(os.getcwd())
    if path is None:
        print(f"이 폴더와 그 위에 {policy.PROJECT_FILE} 가 없습니다")
        return 1
    loaded = policy.load(path, PROJECT)
    if loaded.errors:
        print(f"{path} 에 오류가 있어 승인할 수 없습니다")
        for error in loaded.errors:
            print(f"  {error}")
        return 1
    if policy.is_trusted(path, loaded.digest):
        print(f"이미 승인했습니다  {path}")
        return 0
    print(f"{path}  {loaded.name}  v{loaded.version}")
    for rule in loaded.rules:
        print(f"  {rule.id}  {rule.mode}  {_level(rule)}  {' '.join(rule.command)}  {rule.message}")
        if rule.suggest:
            print(f"      권장  {rule.suggest}  (저장소 규칙의 제안)")
    if not _ask("이 프로젝트 규칙을 승인할까요? 파일이 바뀌면 다시 승인해야 합니다", args.yes):
        print("승인하지 않았습니다")
        return 1
    policy.trust(path, loaded.digest)
    print("승인했습니다")
    return 0


def audit_main(argv):
    parser = argparse.ArgumentParser(prog=f"{APP_NAME} audit", description="감사 기록")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="감사 기록 보고서")
    report.add_argument("files", nargs="*")
    report.add_argument("--since", default="7d")
    report.add_argument("--top", type=int, default=10)
    group = report.add_mutually_exclusive_group()
    group.add_argument("--by-host", action="store_const", const="host", dest="by")
    group.add_argument("--by-user", action="store_const", const="user", dest="by")
    args = parser.parse_args(argv)
    return cmd_audit_report(args)


def _rate(counts):
    rate = audit.stopped_rate(counts)
    return "-" if rate is None else f"{rate * 100:.0f}%"


def _result_cells(counts):
    return [str(sum(counts.values())), str(counts[audit.APPROVED]), str(counts[audit.CANCELLED]),
            str(counts[audit.WRONG_WORD]), str(counts[audit.PASSED]), _rate(counts)]


def cmd_audit_report(args):
    try:
        since = audit.parse_since(args.since) if args.since != "all" else None
    except ValueError:
        print("--since 는 7d, 24h, 30m, all 처럼 씁니다")
        return 1
    summary = audit.summarize_audit(args.files or audit.audit_files(), since=since, by=args.by)
    period = f"{summary.first[5:10]} ~ {summary.last[5:10]}" if summary.first else "기록 없음"
    print(f"기간 {period}   파일 {summary.files}개   기록 {summary.lines:,}줄   "
          f"중복 {summary.duplicates}   읽지 못한 줄 {summary.unreadable}")
    head = ("건수", "승인", "취소", "잘못된 입력", "통과", "멈춘 비율")

    print("\n등급별")
    _print_table([("등급", *head)] + [(f"L{level}", *_result_cells(counts))
                                     for level, counts in sorted(summary.levels.items(), reverse=True)])
    print(f"판정을 끝내지 못한 명령  {summary.unverified}건")

    if summary.rules:
        print("\n팀 규칙별")
        _print_table([("규칙", *head)] + [(name, *_result_cells(counts))
                                         for name, counts in sorted(summary.rules.items())])
    if summary.commands:
        print(f"\n자주 걸린 명령 (상위 {args.top})")
        top = sorted(summary.commands.items(), key=lambda item: (-sum(item[1].values()), item[0]))[:args.top]
        _print_table([("명령", *head)] + [(name, *_result_cells(counts)) for name, counts in top])
    if args.by and summary.by:
        print("\n" + ("서버별" if args.by == "host" else "사용자별"))
        _print_table([("서버" if args.by == "host" else "사용자", *head)]
                     + [(name, *_result_cells(counts)) for name, counts in sorted(summary.by.items())])
    if summary.changes:
        print("\n정책 변경")
        names = {"added": "추가", "changed": "바꿈", "removed": "지움", "refused": "건너뜀"}
        for event in summary.changes:
            versions = " → ".join(f"v{v}" for v in (event.get("before"), event.get("after")) if v is not None)
            reason = f"  ({event['reason']})" if event.get("reason") else ""
            print(f"  {event['time'][5:16].replace('T', ' ')}  {names.get(event['action'], event['action'])}  "
                  f"{event['file']}  {versions}  {event.get('commit', '')}{reason}".rstrip())
    return 0


def cmd_sync(args):
    try:
        return sync.sync()
    except sync.SyncError as e:
        print(e)
        return 1


COMMANDS = {
    "sync": cmd_sync,
    "add": cmd_add, "remove": cmd_remove, "show": cmd_show, "check": cmd_check, "test": cmd_test,
    "explain": cmd_explain, "hits": cmd_hits, "trust": cmd_trust,
}
