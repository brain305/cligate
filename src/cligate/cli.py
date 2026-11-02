from prompt_toolkit import PromptSession, prompt
from . import APP_NAME, parse, builtin, complete, gate, learn, risk, runner, shell


NOT_RUN = {126, 127}  # 셸이 명령을 찾거나 실행하지 못했다. 오타를 학습하지 않는다


def main():
    shell.start_loading()
    # 자동완성은 0.5초 멈췄을 때 IdleTrigger 가 연다. TAB 은 언제든 동작한다
    session = PromptSession(completer=complete.completer(), complete_while_typing=False,
                            key_bindings=complete.key_bindings())
    session.default_buffer.on_text_changed += complete.IdleTrigger()
    while True:
        try:
            line = session.prompt(f"{APP_NAME} > ").strip()
            if not line:
                continue

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
                continue

            # 4. 위험 검사
            verdict = gate.check(commands)
            for finding in verdict.findings:
                print(finding)

            # 5.확인
            # y 는 습관적으로 누를 수 있어서, 가장 위험한 경우는 단어를 직접 치게 한다
            if verdict.level == risk.L3:
                if prompt(f"진행하려면 {verdict.word} 입력: ").strip() != verdict.word:
                    print("취소했습니다. 아무것도 실행하지 않았습니다")
                    continue
            elif verdict.level == risk.L2:
                if prompt("실행할까요? (y/N) ").strip().lower() != "y":
                    continue


            # 6. 실행 - 입력 내용을 쉘에 그대로 넣는 입력
            code = runner.run(line)
            if code not in NOT_RUN:
                learn.record(commands)
        except EOFError:
            break
        except KeyboardInterrupt:
            print()
            continue
