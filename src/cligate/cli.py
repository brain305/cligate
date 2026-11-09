import os
import sys

from . import APP_NAME, __version__, audit, parse, builtin, gate, learn, policy, risk, runner, shell, statusline, ui


REMOTE_WAIT_SECONDS = 1.5
NOT_RUN = {126, 127}  # 셸이 명령을 찾거나 실행하지 못했다. 오타를 학습하지 않는다


def main():
    if sys.argv[1:] == ["--version"]:
        print(f"{APP_NAME} {__version__}")
        return
    if sys.argv[1:2] in (["policy"], ["audit"]):
        from . import policycmd
        sys.exit((policycmd.main if sys.argv[1] == "policy" else policycmd.audit_main)(sys.argv[2:]))
    if sys.platform == "win32":
        # /bin/sh 로 실행하는 구조라 명령을 입력하는 순간 오류로 끝난다. 설치는 되므로 처음에 알린다
        sys.stdout.reconfigure(errors="replace")
        print(f"{APP_NAME}: Windows는 아직 지원하지 않습니다. WSL에서 실행해 주세요.")
        sys.exit(1)
    shell.start_loading()
    monitor = statusline.Monitor()
    monitor.start()
    # 자동완성은 0.5초 멈췄을 때 IdleTrigger 가 연다. TAB 은 언제든 동작한다
    screen = ui.Prompt(APP_NAME, monitor)
    _warn_broken_policies()
    while True:
        try:
            line = screen.read_command().strip()
            if not line:
                continue
            ui.echo(line)

            # 1. 문법 검사
            err = parse.syntax_error(line)
            if err:
                print(err)
                continue

            # 2. 파싱
            try:
                commands = parse.parse(line)
            except ValueError:
                print(f"{APP_NAME}: 입력이 잘못되었습니다")
                continue

            # 3. 내장 명령 실행
            # (cd sub) 는 서브셸 안의 일이므로 직접 처리하지 않고 셸에 넘긴다
            if len(commands) == 1 and commands[0][0] in builtin.BUILTINS and not line.startswith("("):
                name, *args = commands[0]
                builtin.BUILTINS[name](args)
                learn.record(commands)
                monitor.resume()  # cd 뒤에는 새 폴더의 상태를 보여준다
                continue

            # 4. 위험 검사
            verdict = gate.check(commands)
            for finding in verdict.findings:
                ui.show(finding)

            # 5.확인
            # y 는 습관적으로 누를 수 있어서, 가장 위험한 경우는 단어를 직접 치게 한다
            reply = None
            if verdict.level == risk.L3:
                reply = screen.ask(f"진행하려면 {verdict.word} 입력").strip()
                ui.answer(reply)
            elif verdict.level == risk.L2:
                reply = screen.ask("실행할까요? (y/N)").strip()
                ui.answer(reply)
            result = audit.result_of(verdict, reply)
            # 관찰 모드 규칙과 감사 대상은 실행 전 폴더 기준으로 남긴다. 기록에 실패해도 실행에는 영향이 없다
            audit.record_observed(verdict.observed, os.getcwd(), audit.CANCELLED if result in (
                audit.CANCELLED, audit.WRONG_WORD) else audit.PASSED)
            audit.record_decision(verdict, line, commands, os.getcwd(), result)
            if result in (audit.CANCELLED, audit.WRONG_WORD):
                ui.notice("취소했습니다. 아무것도 실행하지 않았습니다")
                continue

            # 6. 실행 - 입력 내용을 쉘에 그대로 넣는 입력
            monitor.pause()
            try:
                code = runner.run(line)
            finally:
                touched = gate.touches_remote(commands)
                monitor.resume(refresh_remote=touched)
            if touched:
                monitor.wait_settled(REMOTE_WAIT_SECONDS)
            if code not in NOT_RUN:
                learn.record(commands)
        except EOFError:
            break
        except KeyboardInterrupt:
            print()
            continue


def _warn_broken_policies():
    """오류가 있거나 승인 전인 규칙 파일은 쓰지 않는다. 모르고 지나치지 않게 시작할 때 한 번 알린다."""
    for loaded in policy.policies(os.getcwd()):
        label = policy.SOURCE_LABEL[loaded.source]
        if loaded.errors:
            ui.notice(f"{APP_NAME}: {label} 파일에 오류가 있어 쓰지 않습니다. {APP_NAME} policy check {loaded.file}")
        elif not loaded.trusted:
            ui.notice(f"{APP_NAME}: {label}이 승인 전이라 쓰지 않습니다. {APP_NAME} policy trust")
