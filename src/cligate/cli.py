from prompt_toolkit import prompt
import subprocess
from . import APP_NAME, parse, builtin, gate


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
            if len(commands) == 1 and commands[0][0] in builtin.BUILTINS:
                name, *args = commands[0]
                builtin.BUILTINS[name](args)
                continue

            # 4. 위험 검사
            warnings = gate.check(commands)
            if warnings:
                for w in warnings:
                    print(f"{w}")
                if prompt("실행할까요? (y/N) ").strip().lower() != "y":
                    continue
            # 5.확인

            # 6. 실행 - 입력 내용을 쉘에 그대로 넣는 입력
            subprocess.run(line, shell=True)
        except EOFError:
            break
        except KeyboardInterrupt:
            print()
            continue
