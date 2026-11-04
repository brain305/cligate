"""셸 기록으로 보고서 2.2.7 의 나머지 지표를 잰다. 맥에서 직접 실행한다.

    .venv/bin/python eval/history.py

잘못 막은 비율: 최근 명령 500건을 임시 저장소에서 판정해 L2 이상인 비율을 센다.
자동완성 3위 안 비율: 기록을 시간 순서로 재생하며, 다음 명령이 그때까지의 기록으로 만든 목록의 몇 위인지 센다.

기록 원문은 저장하지도 출력하지도 않는다. 출력에는 건수와 명령 이름, 옵션 이름만 나온다.
명령은 실행하지 않고, 파일은 임시 폴더 안에만 만들며, git 은 외부 원격에 접속하지 못하게 막는다.
종료 코드: 0 모든 목표 달성, 1 목표 미달, 2 기록을 읽지 못함.
"""
import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIMIT = 500
CASE_SECONDS = 20
BLOCK_TARGET, TOP3_TARGET = 2, 70  # 측정 전에 고정한 목표. 결과를 본 뒤 바꾸지 않는다
ZSH_META = 0x83
ZSH_HEADER = re.compile(rb"^: \d+:\d+;")
MAX_ARG_LENGTH = 100
SHOWN_NAMES = 20


def default_history():
    candidates = [os.environ.get("HISTFILE"), "~/.zsh_history", "~/.bash_history"]
    for candidate in candidates:
        if candidate and os.path.isfile(os.path.expanduser(candidate)):
            return os.path.expanduser(candidate)
    return None


def unmetafy(raw):
    """zsh 는 기록의 일부 바이트를 0x83 뒤에 변형해 저장한다. 한글이 깨지지 않게 되돌린다."""
    out, escaped = bytearray(), False
    for byte in raw:
        if escaped:
            out.append(byte ^ 0x20)
            escaped = False
        elif byte == ZSH_META:
            escaped = True
        else:
            out.append(byte)
    return bytes(out)


def read_history(path):
    """기록 파일의 명령을 오래된 것부터 돌려준다. 읽을 수 없는 줄은 뺀다."""
    entries, current = [], b""
    for raw in Path(path).read_bytes().split(b"\n"):
        current += ZSH_HEADER.sub(b"", raw) if not current else raw
        if current.endswith(b"\\"):  # 여러 줄 명령
            current = current[:-1] + b"\n"
            continue
        entries.append(current)
        current = b""
    lines = []
    for entry in entries:
        try:
            text = unmetafy(entry).decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if text and not text.startswith("#"):
            lines.append(text)
    return lines


def parsed_commands(lines):
    """파싱되는 줄만 (원문, 명령 목록) 으로 돌려준다. 오타나 깨진 줄은 평소 명령이 아니다."""
    from cligate import parse
    found = []
    for line in lines:
        try:
            commands = parse.parse(line)
        except ValueError:
            continue
        if commands and not parse.syntax_error(line):
            found.append((line, commands))
    return found


def _safe_path(word, root):
    """임시 폴더 안에 만들어도 되는 상대 경로면 그 경로를 돌려준다."""
    if not word or word.startswith(("-", "/", "~")) or len(word) > MAX_ARG_LENGTH:
        return None
    if re.search(r"[\s$*?\[\]{}()<>|;&'\"`\\\x00]", word) or ".." in word.split("/") or word in (".", ".git"):
        return None
    if word.split("/")[0] == ".git":
        return None
    path = (root / word.rstrip("/")).resolve()
    return path if root in path.parents else None


def make_arguments(commands, root):
    """git 이 아닌 명령이 가리키는 파일을 만든다. 없으면 지울 대상이 없다고 보아 경고가 줄어들기 때문이다."""
    from cligate import parse
    for argv in commands:
        real, _ = parse.unwrap(argv)
        if not real or os.path.basename(real[0]) == "git":
            continue
        for word in real[1:]:
            path = _safe_path(word, root)
            if path is None or path.exists():
                continue
            try:
                if word.endswith("/"):
                    path.mkdir(parents=True)
                    (path / "file").write_text("x\n")
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("x\n")
            except OSError:
                continue


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def judge_in(base, work, commands, committed):
    """base 를 복사한 저장소에서 판정한다. committed 면 대상이 커밋되어 있고, 아니면 커밋되지 않은 상태이다."""
    from cligate import gate
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(base, work, symlinks=True)
    make_arguments(commands, work)
    if committed:
        _git("add", "-A", cwd=work)
        subprocess.run(["git", "commit", "-q", "-m", "대상"], cwd=work, capture_output=True)
    os.chdir(work)
    return gate.check(commands).level


class TooSlow(Exception):
    pass


def _on_alarm(signum, frame):
    raise TooSlow


def measure_blocking(parsed, base, work):
    """(판정 수, 커밋된 경우 L2 이상 이름, 미커밋 경우 L2 이상 이름, 실패 수) 를 돌려준다."""
    from cligate import learn, risk
    signal.signal(signal.SIGALRM, _on_alarm)
    committed, uncommitted, failed = Counter(), Counter(), 0
    done = 0
    for _, commands in parsed:
        try:
            signal.alarm(CASE_SECONDS)
            first = judge_in(base, work, commands, True)
            second = judge_in(base, work, commands, False)
        except (TooSlow, OSError, subprocess.SubprocessError):
            failed += 1
            continue
        finally:
            signal.alarm(0)
            os.chdir(base)
        done += 1
        names = [learn._entry(argv) or "(이름 숨김)" for argv in commands]
        name = " + ".join(names)
        if first >= risk.L2:
            committed[name] += 1
        if second >= risk.L2:
            uncommitted[name] += 1
    return done, committed, uncommitted, failed


def _rank_of(value, ordered):
    return ordered.index(value) + 1 if value in ordered else None


def replay(parsed):
    """기록을 재생한다. 각 명령은 그보다 앞선 기록만 학습한 목록으로 평가한다."""
    from cligate import complete, gitspec, learn, parse, state
    results = {"명령 이름": Counter(), "git 뒤 명령": Counter()}
    fixed_names = list(complete.COMMAND_ORDER)
    fixed_git = list(gitspec.COMMANDS)

    def score(group, value, fixed, learned):
        counter = results[group]
        counter["전체"] += 1
        for label, ordered in (("기본", fixed), ("기록", learned)):
            place = _rank_of(value, ordered)
            if place and place <= 3:
                counter[f"{label} 3위"] += 1
            if place == 1:
                counter[f"{label} 1위"] += 1

    for _, commands in parsed:
        for argv in commands:
            real, _ = parse.unwrap(argv)
            if not real:
                continue
            name = os.path.basename(real[0])
            score("명령 이름", name, fixed_names, learn.rank(complete.COMMAND_ORDER + learn.frequent()))
            if name == "git":
                sub, _, _ = state.git_subcommand(real, os.getcwd())
                if sub:
                    score("git 뒤 명령", sub, fixed_git, learn.rank([*gitspec.COMMANDS, *learn.frequent("git")], "git"))
        learn.record(commands)
    return results


def percent(part, whole):
    return part / whole * 100 if whole else 0.0


def verdict(ok):
    return "달성" if ok else "미달"


def report(total_lines, parsed_count, done, committed, uncommitted, failed, results):
    print(f"기록 {total_lines}줄, 파싱된 명령 {parsed_count}건")
    if done < LIMIT:
        print(f"  참고  목표 표본 {LIMIT}건보다 적은 {done}건만 판정했습니다")

    blocked, worst = sum(committed.values()), sum(uncommitted.values())
    block_rate = percent(blocked, done)
    print(f"\n잘못 막은 비율 (판정 {done}건, 실패 {failed}건)")
    print(f"  {verdict(block_rate <= BLOCK_TARGET and done > 0)}  대상이 커밋된 상태: L2 이상 {blocked}건 = "
          f"{block_rate:.1f}% (목표 {BLOCK_TARGET}% 이하)")
    print(f"  참고  대상이 커밋되지 않은 상태: L2 이상 {worst}건 = {percent(worst, done):.1f}%")
    if committed or uncommitted:
        print("  L2 이상이 나온 명령 (커밋됨 / 미커밋)")
        for name in sorted(set(committed) | set(uncommitted), key=lambda n: (-committed[n], -uncommitted[n], n))[:SHOWN_NAMES]:
            print(f"    {committed[name]:4d} / {uncommitted[name]:4d}  {name}")

    total = sum(c["전체"] for c in results.values())
    top3 = sum(c["기록 3위"] for c in results.values())
    base3 = sum(c["기본 3위"] for c in results.values())
    rate = percent(top3, total)
    print(f"\n자동완성 3위 안 비율 (예측 {total}건)")
    print(f"  {verdict(rate >= TOP3_TARGET and total > 0)}  사용 기록을 더했을 때: {top3}건 = {rate:.1f}% (목표 {TOP3_TARGET}% 이상)")
    print(f"  참고  기본 순서만 쓸 때: {base3}건 = {percent(base3, total):.1f}%")
    for group, c in results.items():
        print(f"  {group} {c['전체']}건: 3위 안 기본 {percent(c['기본 3위'], c['전체']):.1f}% → 기록 {percent(c['기록 3위'], c['전체']):.1f}%, "
              f"1위 기본 {percent(c['기본 1위'], c['전체']):.1f}% → 기록 {percent(c['기록 1위'], c['전체']):.1f}%")
    return block_rate <= BLOCK_TARGET and rate >= TOP3_TARGET and done > 0 and total > 0


def isolate(temp):
    """이후의 판정과 학습이 사용자의 설정, 기록, 외부 원격에 닿지 않게 한다. cligate 를 불러오기 전에 부른다."""
    os.environ.update(HOME=str(temp), XDG_STATE_HOME=str(temp / "state"), GIT_CONFIG_NOSYSTEM="1",
                      GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="file",
                      GIT_AUTHOR_NAME="me", GIT_AUTHOR_EMAIL="me@test", GIT_COMMITTER_NAME="me",
                      GIT_COMMITTER_EMAIL="me@test")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "HISTFILE"):
        os.environ.pop(name, None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", help="기록 파일. 없으면 HISTFILE, ~/.zsh_history, ~/.bash_history 순서로 찾는다")
    parser.add_argument("--limit", type=int, default=LIMIT, help="판정할 최근 명령 수")
    args = parser.parse_args()
    path = args.history or default_history()
    if not path or not os.path.isfile(path):
        print("셸 기록 파일을 찾지 못했습니다. --history 로 지정해 주세요", file=sys.stderr)
        return 2
    lines = read_history(path)
    start = os.getcwd()

    with tempfile.TemporaryDirectory() as raw:
        temp = Path(os.path.realpath(raw))
        (temp / ".zshrc").touch()
        isolate(temp)
        (temp / "repo").mkdir()
        subprocess.run(["bash", "-c", f'set -e; source "{HERE / "fixture.sh"}"; make_fixture "{temp}/repo"'],
                       check=True, capture_output=True, cwd=temp)
        base = temp / "repo" / "work"
        os.chdir(base)
        try:
            parsed = parsed_commands(lines)
            if not parsed:
                print("파싱되는 명령이 없습니다", file=sys.stderr)
                return 2
            done, committed, uncommitted, failed = measure_blocking(parsed[-args.limit:], base, temp / "case")
            results = replay(parsed)
        finally:
            os.chdir(start)
        ok = report(len(lines), len(parsed), done, committed, uncommitted, failed, results)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
