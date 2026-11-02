from prompt_toolkit import prompt
from . import APP_NAME, parse, builtin, gate, risk, runner


def main():
    while True:
        try:
            # input() 대신 prompt_toolkit 사용 — 나중에 자동완성/실시간 입력 처리를 붙이기 위해
            line = prompt(f"{APP_NAME} > ").strip()
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
            runner.run(line)
        except EOFError:
            break
        except KeyboardInterrupt:
            print()
            continue
