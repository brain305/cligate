import os
import shlex
import subprocess
import tempfile


def run(line):
    """한 줄을 셸에서 실행하고, 셸이 끝난 위치로 CliGate 도 따라간다. 종료 코드를 돌려준다."""
    # cd sub && ls 의 cd 는 자식 셸 안에서만 일어나므로, 끝날 때 위치를 파일로 받아온다
    fd, marker = tempfile.mkstemp(prefix="cligate-")
    os.close(fd)
    try:
        # trap EXIT 는 exit 로 끝나도 실행되고 종료 코드도 바꾸지 않는다
        save_cwd = f'printf %s "$PWD" > {shlex.quote(marker)}'
        code = subprocess.run(f"trap {shlex.quote(save_cwd)} EXIT; {line}", shell=True).returncode
        with open(marker, encoding="utf-8") as f:
            _follow(f.read())
    finally:
        os.unlink(marker)
    return code


def _follow(new_cwd):
    old_cwd = os.getcwd()
    if not new_cwd or not os.path.isdir(new_cwd):
        return
    if os.path.realpath(new_cwd) == os.path.realpath(old_cwd):
        return
    os.chdir(new_cwd)
    # builtin.cd 와 같이 갱신해야 다음 cd - 가 맞게 동작한다
    os.environ["OLDPWD"] = old_cwd
    os.environ["PWD"] = os.getcwd()
