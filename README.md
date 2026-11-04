# cligate

2026 capstone design
### demo
https://claude.ai/artifact/AcsVWs6MzH6XGwon5VX43c

### 보고서
https://claude.ai/artifact/SC1tbrYQvadwi8UzTjNLY7

### 설계
https://claude.ai/artifact/NtPzq6Cd3meWjb2aVKKMeJ

## CliGate가 하는 일

터미널에 입력한 명령을 셸보다 먼저 받아, 지금 저장소와 파일 상태를 보고 위험하면 실행 전에 알리고 막습니다. 같은 정보로 자동완성 순서도 정합니다.

| 등급 | 반응                         | 예                                         |
| ---- | ---------------------------- | ------------------------------------------ |
| L0   | 바로 실행                    | `ls`, 변경이 없을 때 `git reset --hard`    |
| L1   | 알리고 실행                  | 커밋된 파일 `rm`                           |
| L2   | 근거를 보여주고 `y/N` 확인   | 미추적 파일 `rm`, 변경이 있을 때 `git reset --hard` |
| L3   | 단어(`delete`, `force`) 입력 | `rm -rf /`, 팀원 커밋을 덮는 `git push --force` |

```
❯ git push --force
╭─ [L3] git push --force: origin/feature/login 의 커밋 2개가 사라집니다 ───────────╮
│ 47d86a4  fix: 로그인 리다이렉트 수정  (팀원, 12초 전)                            │
│ b9b5d22  test: 로그인 테스트 추가  (팀원, 12초 전)                               │
│ 권장  git push --force-with-lease --force-if-includes                            │
╰──────────────────────────────────────────────────────────────────────────────────╯
╭─ 진행하려면 force 입력 ──────────────────────────────────────────────────────────╮
│ >                                                                                │
╰──────────────────────────────────────────────────────────────────────────────────╯
```

입력창 아래에는 브랜치와 원격 상태가 항상 표시됩니다 (`↓2 pull 필요`, `↑1 push 대기`, `↓2 ↑1 갈라짐`). 작업 중인 파일도 함께 보입니다 (`✎3 add 필요`, `●1 커밋 대기`). 1분마다 원격에 직접 물어보며, `origin/main` 같은 추적 정보는 바꾸지 않습니다.

입력이 0.5초 멈추면 자동완성 목록이 뜹니다. TAB은 바로 열립니다. 위쪽 5개 후보에는 등급 배지가 붙습니다 (L1 청록, L2 노랑, L3 빨강). 위험한 후보는 맨 아래에 `⚠`로 표시됩니다. 입력 중의 등급은 원격을 확인하지 않은 추정값이고, 실제 판정은 엔터를 누른 뒤에 합니다.

입력 중이거나 목록에서 고른 명령이 L1 이상이면 입력창 테두리가 그 등급의 색으로 바뀌고, 제목에 추정 등급이 표시됩니다 (L1 청록, L2 노랑, L3 빨강). 지금 쓴 단어와 똑같은 후보는 위험 여부와 상관없이 목록 맨 위에 옵니다.

## 지원 환경

| 환경 | 상태 | 확인한 범위 |
| --- | --- | --- |
| macOS | 지원 | GitHub Actions에서 설치, 판정, 터미널 동작 |
| Ubuntu, RHEL 계열 | 지원 | GitHub Actions와 컨테이너에서 설치, 판정 |
| WSL | 지원 | WSL 2(Ubuntu 26.04)에서 설치, 실행, 터미널 동작, 전체 테스트. WSL 1에서 설치와 판정. `/mnt/c` 속도는 미확인 |
| Windows | 미지원 | 안내를 출력하고 종료합니다. WSL을 사용하세요 |

## 설치

Python 3.9 이상, git이 필요합니다.

**1. pipx 설치**

```bash
brew install pipx && pipx ensurepath                 # macOS
sudo apt update && sudo apt install pipx && pipx ensurepath   # Ubuntu, Debian, WSL
python3 -m ensurepip --user && python3 -m pip install --user pipx && python3 -m pipx ensurepath   # RHEL, Rocky
```

**2. 새 터미널을 열고 CliGate 설치**

```bash
pipx install git+https://github.com/brain305/cligate.git
cligate
```

끝내려면 `exit`를 입력합니다. `cligate: command not found`가 나오면 터미널을 새로 열어보세요. 설치된 버전은 `cligate --version`으로 확인합니다.

**업데이트와 삭제**

```bash
pipx upgrade cligate
pipx uninstall cligate
```

## 버전별 변경 사항

이미 설치했다면 `pipx upgrade cligate`로 최신 버전으로 올립니다. 특정 버전을 설치하려면 주소 뒤에 태그를 붙입니다.

```bash
pipx install --force git+https://github.com/brain305/cligate.git@v0.1.0
```

### v0.2.1 (2026-11-05)

**화면**

- 입력 중이거나 목록에서 고른 명령이 L1 이상이면 입력창 테두리가 그 등급의 색으로 바뀌고, 제목에 추정 등급을 표시합니다 (L1 청록, L2 노랑, L3 빨강).
- 평소 테두리는 터미널 기본 글자색을 씁니다.
- 프롬프트 기호를 `❯`로 통일했습니다.

**자동완성**

- 지금 쓴 단어와 똑같은 후보를 위험 여부와 상관없이 목록 맨 위에 둡니다.
- 선택 줄을 강조하고 등급을 배경색 배지로 표시합니다.
- 목록 아래에 위치와 키 안내를 표시합니다.

**환경**

- `cligate --version`으로 설치된 버전을 확인합니다.

### v0.2.0 (2026-11-04)

**화면**

- 테두리 입력창과 L2, L3 경고 상자를 추가했습니다.
- 입력창 아래에 브랜치와 원격 상태, 작업 파일 수를 항상 표시합니다 (`↓2 pull 필요`, `↑1 push 대기`, `✎3 add 필요`, `●1 커밋 대기`). push, pull, fetch 뒤에는 바로 다시 확인합니다.

**위험 판정**

- 받지 않은 커밋이 있는 `git push`는 그 커밋을 보여주고 확인을 받습니다.
- 다른 브랜치를 올리는 `git push`를 알립니다. `main`과 `master`는 확인을 받습니다.
- 받지 않은 커밋이 있을 때 `git commit`을 하면 먼저 받을 수 있다고 알립니다.
- 하위 폴더에서 `git add .`를 하면 바깥에 남는 파일을 알립니다.
- WSL에서 보이는 Windows 주요 폴더 삭제를 L3로 판정합니다.

**자동완성**

- 위쪽 5개 후보에 등급 배지를 표시합니다 (L1 청록, L2 노랑, L3 빨강).
- 상태 표시줄의 알림 순서대로 할 일을 맨 위에 올립니다 (충돌 해결, 받기, `add`, `commit`, `push`).
- `git pull` 뒤에 브랜치와 받을 커밋 수를 보여줍니다.

**환경과 측정**

- Windows에서 실행하면 WSL을 안내하고 종료합니다.
- 보고서의 검증 지표를 재는 프로그램을 추가했습니다 (`eval/`). `docker/run.sh eval`로 실행합니다.
- 지원 환경을 넓혀 자동 시험을 돌립니다.

### v0.1.0 (2026-11-02)

첫 공개 버전입니다.

- 입력 루프, 사용자 셸로 실행, 내장 명령 (`cd`, `exit`, `export`, `unset`)
- 명령 파싱과 문법 검사
- 복구 가능성과 영향 범위로 정하는 L0~L3 위험 판정 (`rm`, `mv`, `cp`, `>`, git 작업 손실, 강제 푸시, 원격 삭제)
- 근거를 보여주는 확인과 차단
- 0.5초 멈추면 뜨는 자동완성과 사용 기록 학습

## 주의

CliGate는 **실수를 줄이는 도구이지 보안 도구가 아닙니다.** 다음은 막지 못합니다.

- CliGate 밖에서 실행한 명령, 스크립트 안의 명령 (`./clean.sh`, `python -c "..."`)
- 아직 판정하지 않는 명령 (`git rebase`, `tee`, `dd`, `sed -i` 등)
- 셸 함수 안의 명령 (별칭은 풀어서 검사합니다)
- `delete`, `force`를 직접 입력해 확인한 명령

경고가 없다고 안전하다는 뜻은 아닙니다. 중요한 데이터는 따로 백업하세요.

`.zshrc`, `.bashrc`의 별칭은 시작할 때 한 번 읽습니다. 셸 함수(`nvm`, `conda activate` 등)는 동작하지 않습니다. 실행한 명령의 이름과 옵션 이름은 자동완성 순서를 위해 `~/.local/state/cligate/history`에 기록합니다. 인자 값은 저장하지 않으며, 이 파일을 지우면 기본 순서로 돌아갑니다.

## 시연과 시험

위험한 명령은 실제 컴퓨터가 아닌 Docker 컨테이너에서 시험하세요. 맥의 폴더를 연결하지 않고, 나가면 모두 사라집니다.

```bash
docker/run.sh        # 시연 환경. 명령 목록은 docker/demo-commands.md
docker/run.sh test   # 리눅스에서 전체 테스트
docker/run.sh eval   # 보고서 지표 케이스 70건 측정
```

## 파일 역할

| 파일          | 역할                                                                |
| ------------- | ------------------------------------------------------------------- |
| `cli.py`      | 진입점. 입력 루프, 확인과 차단                                      |
| `parse.py`    | 명령 분리(`&&`, `\|`, `;`), 문법 검사                               |
| `builtin.py`  | `cd`, `exit`, `export`, `unset` — 셸에 넘기면 상태가 안 남는 명령들 |
| `gate.py`     | 위험 판정의 중심. 명령마다 알맞은 판정으로 보낸다                   |
| `risk.py`     | L0~L3 등급, 복구 가능성과 영향 범위의 결정표                        |
| `fileops.py`  | `mv`, `cp`, `>` 덮어쓰기 판정                                       |
| `gitwork.py`  | `reset`, `clean`, `checkout`, `restore`, `switch`, `branch`, `stash`, `commit`, `add` 판정 |
| `gitpush.py`  | 강제 푸시, 원격 삭제, 평범한 push 판정                              |
| `state.py`    | git 상태 조사                                                       |
| `complete.py` | 0.5초 멈추면 뜨는 자동완성                                          |
| `gitspec.py`  | 자동완성에 쓰는 git 명령 표                                         |
| `learn.py`    | 최근 명령 1000개로 후보 순서 갱신                                   |
| `shell.py`    | 사용자 셸과 별칭                                                    |
| `statusline.py` | 입력창 아래의 원격 상태. 1분마다 백그라운드에서 조사              |
| `ui.py`       | 입력창, 경고 상자, 상태 줄을 그림                                   |
| `runner.py`   | 셸로 실행하고 위치를 따라감                                         |

## 진행 현황

| 단계 | 내용                        | 상태 |
| ---- | --------------------------- | ---- |
| 1    | 설치 배선, `cligate` 명령어 | ✅   |
| 2    | 입력 루프, 셸 위임, 빌트인  | ✅   |
| 3    | 명령 파싱, 문법 검사        | ✅   |
| 4    | 위험 판정                   | ✅   |
| 5    | git 상태 수집               | ✅   |
| 6    | 확인·차단 게이트            | ✅   |
| 7    | 0.5초 멈추면 뜨는 자동완성  | ✅   |
| 8    | 입력창 아래의 원격 상태 표시 | ✅  |
| 9    | 보고서의 검증 지표 측정     | ✅   |

## 개발

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

## 라이선스

[MIT](LICENSE)
