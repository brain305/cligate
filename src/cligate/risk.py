import os
from dataclasses import dataclass, field

L0, L1, L2, L3 = 0, 1, 2, 3  # 바로 실행, 알림 후 실행, y/N 확인, 단어 입력
R0, R1, R2, R3 = 0, 1, 2, 3  # 안 바뀜, 명령 하나로 되돌림, 기록에서 되돌림, 되돌릴 수 없음
S0, S1, S2 = 0, 1, 2  # 대상 없음, 작음, 큼 또는 공유 자원

# 행이 R, 열이 S. R3·S0 은 잃을 것의 크기를 모를 때라 L2 로 묻는다
MATRIX = (
    (L0, L0, L0),
    (L0, L1, L1),
    (L1, L1, L2),
    (L2, L2, L3),
)
MAX_FILES = 50
MAX_BYTES = 100 * 1024 * 1024
MAX_LISTED = 8
DEFAULT_WORD = "delete"


@dataclass
class Finding:
    level: int
    message: str
    word: str = DEFAULT_WORD

    def __str__(self):
        return f"[L{self.level}] {self.message}"


@dataclass
class Verdict:
    """한 줄 전체의 판정. 등급은 그 안에서 가장 높은 것을 따른다."""
    findings: list = field(default_factory=list)

    @property
    def level(self):
        return max((f.level for f in self.findings), default=L0)

    @property
    def word(self):
        return next((f.word for f in self.findings if f.level == L3), None)


def judge(r, s, message, word=DEFAULT_WORD):
    return Finding(MATRIX[r][s], message, word)


def judge_files(r, paths, message, word=DEFAULT_WORD):
    """대상 파일의 개수와 용량으로 영향 범위를 재서 판정한다. 확인을 받을 때는 규모도 보여준다."""
    scope, size = measure(paths)
    finding = judge(r, scope, message, word)
    if finding.level >= L2 and scope != S0:
        finding.message += f"\n    규모: {size}"
    return finding


def measure(paths):
    """(영향 범위, 규모 문구) 를 돌려준다. 경계를 넘으면 더 세지 않아 큰 폴더에서도 멈추지 않는다."""
    count = 0
    total = 0
    for path in _files(paths):
        count += 1
        try:
            total += os.lstat(path).st_size
        except OSError:
            continue
        if count >= MAX_FILES:
            return S2, f"파일 {MAX_FILES}개 이상"
        if total >= MAX_BYTES:
            return S2, f"{_format_bytes(MAX_BYTES)} 이상"
    return (S1 if count else S0), f"파일 {count}개, {_format_bytes(total)}"


def describe(title, items, cwd):
    """제목 아래에 (경로, 이유) 목록을 붙인다."""
    lines = [title]
    for path, reason in items[:MAX_LISTED]:
        shown = os.path.relpath(path, os.path.realpath(cwd))
        if shown == "." or shown.startswith(".."):
            shown = os.path.normpath(path)  # 현재 폴더 자신과 그 바깥은 전체 경로가 읽기 쉽다
        lines.append(f"    {shown}  ({reason})")
    if len(items) > MAX_LISTED:
        lines.append(f"    ... 외 {len(items) - MAX_LISTED}개")
    return "\n".join(lines)


def list_commits(title, commits):
    """제목 아래에 커밋이나 항목 목록을 붙인다."""
    lines = [title, *(f"    {commit}" for commit in commits[:MAX_LISTED])]
    if len(commits) > MAX_LISTED:
        lines.append(f"    ... 외 {len(commits) - MAX_LISTED}개")
    return "\n".join(lines)


def _files(paths):
    for path in dict.fromkeys(paths):
        if os.path.isdir(path) and not os.path.islink(path):
            for root, _, names in os.walk(path):
                for name in names:
                    yield os.path.join(root, name)
        else:
            yield path


def _format_bytes(size):
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"
