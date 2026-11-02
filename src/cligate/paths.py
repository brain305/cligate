import glob
import os
import re

SYSTEM_DIRS = {"/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/opt", "/sbin", "/usr", "/var",
               "/Applications", "/Library", "/System", "/Users"}


def fill_vars(word):
    """변수를 값으로 채운다. 셸처럼 없는 변수는 빈 문자열이 되므로 rm -rf $X/ 는 / 가 된다."""
    return re.sub(r"\$\{?(\w+)\}?", lambda m: os.environ.get(m.group(1), ""), word)


def resolve(word, cwd):
    word = os.path.expanduser(word)
    return os.path.normpath(word if os.path.isabs(word) else os.path.join(cwd, word))


def is_critical(path):
    home = os.path.realpath(os.path.expanduser("~"))
    real = os.path.realpath(path)
    is_home_or_above = real == home or home.startswith(real.rstrip(os.sep) + os.sep)
    return path in SYSTEM_DIRS or real in SYSTEM_DIRS or is_home_or_above


def expand(words, cwd):
    """글롭과 ~ 를 펼쳐 실제로 있는 경로만 돌려준다. 셸이 실행하기 전에 미리 계산한다."""
    found = []
    for word in words:
        word = os.path.expanduser(word)
        absolute = word if os.path.isabs(word) else os.path.join(cwd, word)
        if any(c in word for c in "*?["):
            base = word if os.path.isabs(word) else os.path.join(glob.escape(cwd), word)
            # zsh 의 ** 는 아래 폴더까지 펼친다. 다른 셸에서는 덜 지우므로 넓게 봐도 안전하다
            found.extend(sorted(glob.glob(base, recursive=True)))
        elif os.path.lexists(absolute):
            found.append(absolute)
    return found
