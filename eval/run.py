"""보고서 2.2.7 의 지표를 잰다. eval/cases.yaml 의 케이스를 차례로 돌리고 결과와 목표를 표로 보여준다.

케이스마다 임시 폴더에 저장소를 새로 만들고, 프로세스도 따로 띄워 서로 영향을 주지 않게 한다.
명령은 판정과 자동완성 함수에만 넘기고 실행하지 않는다. runner 를 부르는 곳이 없다.
종료 코드: 0 모든 목표 달성, 1 목표 미달, 2 케이스를 돌리지 못함.
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
TIMEOUT_SECONDS = 60
LEVELS = ("L0", "L1", "L2", "L3")
# 측정 전에 고정한 목표. 결과를 본 뒤 바꾸지 않는다
MATCH_TARGET, MISS_TARGET, FIRST_TARGET, COMPLETE_MS_TARGET, JUDGE_MS_TARGET = 90, 0, 80, 300, 200


def collect_status():
    """실제 앱처럼 상태 수집기가 원격을 한 번 확인한 뒤에 입력을 받는다."""
    from cligate import shell, statusline
    shell.start_loading()
    shell.aliases(wait=True)  # 실제로는 시작할 때 읽어 둔다. 그 시간은 응답 시간이 아니다
    monitor = statusline.Monitor().start()
    limit = time.monotonic() + TIMEOUT_SECONDS / 2
    while monitor.status is None and time.monotonic() < limit:
        time.sleep(0.01)
    monitor.stop()


def judge_one(case):
    """실제 입력 흐름의 문법 검사, 파싱, 판정만 돌린다. 실행 단계는 부르지 않는다."""
    from cligate import gate, parse
    line = case["command"]
    collect_status()
    if parse.syntax_error(line):
        return {"error": "문법 오류로 판정"}
    started = time.perf_counter()
    verdict = gate.check(parse.parse(line))
    return {"level": verdict.level, "ms": (time.perf_counter() - started) * 1000,
            "messages": [f.message.split("\n")[0] for f in verdict.findings]}


def complete_one(case):
    """자동으로 뜨는 목록과 같은 조건으로 후보를 받는다. 제한 시간을 넘기면 실제로 안 뜨므로 빈 목록이다."""
    from cligate import complete
    collect_status()
    started = time.monotonic()
    found = complete.candidates(case["input"], os.getcwd(), started + complete.BUDGET_SECONDS, curated=True)
    seconds = time.monotonic() - started
    values = [] if seconds > complete.BUDGET_SECONDS else [c.value for c in found]
    return {"values": values[:5], "ms": seconds * 1000}


def child_main(case_json):
    case = json.loads(case_json)
    print(json.dumps(judge_one(case) if "command" in case else complete_one(case), ensure_ascii=False))


def run_case(case):
    with tempfile.TemporaryDirectory() as temp:
        temp = os.path.realpath(temp)
        env = {k: v for k, v in os.environ.items()
               if k not in ("X", "BUILD", "XDG_STATE_HOME", "GIT_DIR", "GIT_WORK_TREE")}
        env.update(HOME=temp, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
        Path(temp, ".zshrc").touch()
        script = f'set -e; source "{HERE / "fixture.sh"}"; make_fixture "{temp}/repo"; {case["setup"] or "true"}'
        os.mkdir(f"{temp}/repo")
        made = subprocess.run(["bash", "-c", script], env=env, cwd=temp, capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS)
        if made.returncode:
            return {"error": f"상태 만들기 실패: {made.stderr.strip()[-120:]}"}
        done = subprocess.run([sys.executable, __file__, "--one", json.dumps(case, ensure_ascii=False)], env=env,
                              cwd=f"{temp}/repo/work", capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if done.returncode:
            return {"error": done.stderr.strip()[-200:]}
        return json.loads(done.stdout)


def safely(case):
    try:
        return run_case(case)
    except subprocess.TimeoutExpired:
        return {"error": f"{TIMEOUT_SECONDS}초 안에 끝나지 않음"}


def percentile_95(values):
    ordered = sorted(values)
    return ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else 0.0


def verdict(ok):
    return "달성" if ok else "미달"


def show_judgement(cases):
    print(f"판정 {len(cases)}건")
    rows, errors = [], 0
    for case in cases:
        result = safely(case)
        if "error" in result:
            errors += 1
            print(f"  !  {case['id']}  {case['command']}  오류: {result['error']}")
            continue
        got = LEVELS[result["level"]]
        rows.append((case, got, result["ms"]))
        mark = "O" if got == case["expected"] else "X"
        print(f"  {mark}  {case['id']}  기대 {case['expected']}  결과 {got}  {result['ms']:6.0f}ms  "
              f"{case['command']}  ({case['state']})")
        if got != case["expected"]:
            print(f"        {result['messages'][0] if result['messages'] else '알림 없음'}")
    return rows, errors


def show_scenarios(cases):
    print(f"\n상태 시나리오 {len(cases)}건")
    rows, errors = [], 0
    for case in cases:
        result = safely(case)
        if "error" in result:
            errors += 1
            print(f"  !  {case['id']}  {case['input']!r}  오류: {result['error']}")
            continue
        first = result["values"][0] if result["values"] else None
        rows.append((case, first, result["ms"]))
        mark = "O" if first == case["expected_first"] else "X"
        print(f"  {mark}  {case['id']}  기대 {case['expected_first']}  결과 {first or '(목록 없음)'}  "
              f"{result['ms']:6.0f}ms  {case['input']!r}  ({case['state']})")
        if first != case["expected_first"]:
            print(f"        후보: {', '.join(result['values']) or '없음'}")
    return rows, errors


def summarize(judged, scenarios):
    """지표를 계산해 (이름, 측정값, 목표, 달성) 을 돌려준다."""
    matched = sum(got == case["expected"] for case, got, _ in judged)
    missed = [case["id"] for case, got, _ in judged if case["expected"] >= "L2" and got <= "L1"]
    excess = sum(got > case["expected"] for case, got, _ in judged)
    first = sum(got == case["expected_first"] for case, got, _ in scenarios)
    match_rate = matched / len(judged) * 100 if judged else 0
    first_rate = first / len(scenarios) * 100 if scenarios else 0
    judge_ms = percentile_95([ms for *_, ms in judged])
    complete_ms = percentile_95([ms for *_, ms in scenarios])
    return [
        ("기대 등급 일치율", f"{matched}/{len(judged)} = {match_rate:.0f}%", f"{MATCH_TARGET}% 이상", match_rate >= MATCH_TARGET),
        ("위험 놓침", f"{len(missed)}건 {' '.join(missed)}".strip(), f"{MISS_TARGET}건", len(missed) <= MISS_TARGET),
        ("상태 추천 1위 비율", f"{first}/{len(scenarios)} = {first_rate:.0f}%", f"{FIRST_TARGET}% 이상", first_rate >= FIRST_TARGET),
        ("판정 응답 시간 95%", f"{judge_ms:.0f}ms", f"{JUDGE_MS_TARGET}ms 이내", judge_ms <= JUDGE_MS_TARGET),
        ("자동완성 응답 시간 95%", f"{complete_ms:.0f}ms", f"{COMPLETE_MS_TARGET}ms 이내", complete_ms <= COMPLETE_MS_TARGET),
    ], excess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(HERE / "cases.yaml"))
    parser.add_argument("--one", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.one:
        child_main(args.one)
        return 0

    data = yaml.safe_load(Path(args.cases).read_text(encoding="utf-8"))
    judged, judge_errors = show_judgement(data.get("judgement", []))
    scenarios, scenario_errors = show_scenarios(data.get("state_scenarios", []))
    errors = judge_errors + scenario_errors
    metrics, excess = summarize(judged, scenarios)

    print("\n지표 결과")
    for name, value, target, ok in metrics:
        print(f"  {verdict(ok)}  {name}: {value} (목표 {target})")
    print(f"  참고  기대보다 높게 판정: {excess}건")
    print(f"\n실행 {len(judged) + len(scenarios)}건, 오류 {errors}건")
    if errors:
        print("돌리지 못한 케이스가 있어 위 지표는 전체 결과가 아닙니다")
        return 2
    return 0 if all(ok for *_, ok in metrics) else 1


if __name__ == "__main__":
    sys.exit(main())
