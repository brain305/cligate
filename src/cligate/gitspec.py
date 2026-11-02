"""자동완성에 쓰는 git 명령 표. 명령의 경우의 수는 코드가 아니라 여기에 적는다.

새 명령을 추천하거나 설명을 고칠 때는 이 표만 바꾸면 된다. 표에 없는 명령과 옵션도
TAB 을 누르거나 표에 맞는 후보가 없을 때는 git 이 알려준 목록에서 나온다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Spec:
    desc: str = ""             # 후보 옆 설명. <이름> 같은 예시는 줄에 넣지 않는다. 셸이 리다이렉트로 읽기 때문이다
    space: bool = False        # 고르면 공백까지 넣어 다음 후보가 이어서 뜨게 한다
    first: tuple = ()          # 서브커맨드 바로 뒤에 자주 오는 인자 (값, 설명)
    options: tuple = ()        # 자주 쓰는 옵션 (값, 설명). 이 순서대로 보여준다
    max_args: int = None       # 옵션을 뺀 인자를 이만큼 쓰면 더 추천하지 않는다
    done_after: tuple = ()     # 이 인자를 쓰면 더 추천하지 않는다


# 위의 12개는 보고서의 기본 순서이다
COMMANDS = {
    "status": Spec("변경 상태 보기"),
    "add": Spec("스테이지에 올리기 · <파일>", space=True,
                first=((".", "변경 전부"), ("-p", "골라서 올리기")),
                options=(("--all", "삭제까지 전부"), ("--patch", "골라서 올리기")),
                done_after=(".", "-A", "--all")),
    "commit": Spec('변경 기록하기 · -m "<메시지>"', space=True,
                   first=(("-m", "<메시지>"), ("--amend", "직전 커밋 고치기")),
                   options=(("--amend", "직전 커밋 고치기"), ("--all", "추적 중인 변경 전부")),
                   done_after=("-m", "--message")),
    "push": Spec("원격으로 보내기 · [원격] [브랜치]", space=True, max_args=2,
                 options=(("--set-upstream", "추적 브랜치 설정"), ("--force", "원격을 덮어씀"),
                          ("--force-with-lease", "원격이 바뀌었으면 거부"), ("--dry-run", "보내지 않고 확인"),
                          ("--tags", "태그도 함께"))),
    "pull": Spec("원격에서 가져오기 · [원격] [브랜치]", space=True, max_args=2,
                 options=(("--rebase", "내 커밋을 위로 얹기"), ("--ff-only", "빨리감기만 허용"))),
    "switch": Spec("브랜치 이동 · <브랜치>", space=True, max_args=1,
                   first=(("-c", "새 브랜치 만들기 <이름>"),)),
    "branch": Spec("브랜치 관리 · [-d] <이름>", space=True, max_args=1,
                   first=(("-d", "브랜치 지우기 <브랜치>"), ("-a", "원격까지 전부 보기"))),
    "log": Spec("커밋 기록 보기", first=(("--oneline", "한 줄씩"), ("--graph", "그래프로"))),
    "diff": Spec("변경 내용 보기", first=(("--staged", "스테이지된 변경"),)),
    "stash": Spec("변경 잠시 치워두기", space=True, max_args=1,
                  first=(("pop", "치워둔 변경 복원"), ("list", "목록 보기"), ("drop", "치워둔 변경 버리기"))),
    "merge": Spec("브랜치 합치기 · <브랜치>", space=True, max_args=1,
                  options=(("--no-ff", "병합 커밋 남기기"), ("--abort", "병합 취소"), ("--squash", "하나로 합치기"))),
    "rebase": Spec("커밋 다시 얹기 · <브랜치>", space=True, max_args=1,
                   options=(("--continue", "충돌 해결 뒤 계속"), ("--abort", "rebase 취소"), ("--skip", "이 커밋 건너뛰기"),
                            ("--interactive", "커밋 정리하기"))),

    "fetch": Spec("원격 정보만 가져오기", max_args=1, options=(("--all", "모든 원격"), ("--prune", "지워진 브랜치 정리"))),
    "checkout": Spec("브랜치 이동, 파일 되돌리기", space=True, max_args=1,
                     first=(("-b", "새 브랜치 만들기 <이름>"), (".", "변경 전부 되돌리기")),
                     options=(("--force", "변경을 버리고 이동"),)),
    "restore": Spec("파일 되돌리기 · <파일>", space=True,
                    first=((".", "변경 전부 되돌리기"), ("--staged", "스테이지만 내리기")),
                    done_after=(".",)),
    "reset": Spec("커밋 되돌리기 · [--hard] [커밋]", space=True, max_args=1,
                  first=(("HEAD~1", "마지막 커밋 취소, 변경은 유지"), ("--hard", "작업 폴더까지 되돌림")),
                  options=(("--soft", "커밋만 되돌림"), ("--hard", "작업 폴더까지 되돌림")),
                  done_after=("--hard",)),
    "clean": Spec("미추적 파일 지우기 · -n 으로 먼저 확인", space=True,
                  first=(("-n", "지울 파일만 보기"), ("-fd", "파일과 폴더 삭제")),
                  options=(("--dry-run", "지울 파일만 보기"), ("--force", "실제로 삭제"))),
    "show": Spec("커밋 내용 보기"),
    "tag": Spec("태그 관리"),
    "remote": Spec("원격 관리", first=(("-v", "주소까지 보기"), ("add", "원격 추가"))),
    "clone": Spec("저장소 복제 · <주소>", space=True),
    "init": Spec("새 저장소 만들기"),
    "cherry-pick": Spec("다른 커밋 가져오기 · <커밋>", space=True),
    "revert": Spec("커밋을 취소하는 커밋 · <커밋>", space=True),
    "rm": Spec("파일 지우고 기록 · <파일>", space=True),
    "mv": Spec("파일 이동 기록 · <원래> <새 이름>", space=True),
    "mergetool": Spec("충돌 해결 도구 열기"),
}

# 상태 규칙이 추천하는 옵션 붙은 명령
RULE_HINTS = {
    "rebase --continue": "충돌 해결 뒤 계속",
    "rebase --abort": "rebase 취소",
    "pull --rebase": "내 커밋을 위로 얹기",
    "switch -c": "새 브랜치 만들기 <이름>",
}
SPACE_AFTER = {"switch -c", "commit -m"}  # 공백 뒤에 이름이나 메시지를 바로 쓰는 명령

TAKES_VALUE = {"-m", "-d", "-c", "-b"}  # 고르면 공백까지 넣는 인자
# 바로 뒤에 새 이름이나 메시지처럼 직접 쓰는 값이 와서 추천할 것이 없는 옵션
FREE_TEXT_OPTIONS = {"-m", "--message", "-c", "-C", "-b", "-B", "--orphan"}


def spec(sub):
    return COMMANDS.get(sub, Spec())


def hint(sub, value):
    """서브커맨드의 인자나 옵션 설명."""
    return dict((*spec(sub).options, *spec(sub).first)).get(value, "")


def takes_space(value):
    return spec(value).space or value in SPACE_AFTER
