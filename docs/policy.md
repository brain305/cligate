# 팀 규칙(정책) 기능 설계

기준: CliGate v0.2.1. 모든 기능은 하나의 공개 코드(MIT)로 만든다.

## 1. 목적과 원칙

**목적:** 팀이 자기 환경에 맞는 차단 규칙을 만들어 여러 서버에 배포하고, 위험한 명령의 실행 기록을 남긴다.

**원칙**

1. **올리기만 한다.** 팀 규칙은 등급을 높이거나 새 명령을 막을 수만 있고, 기본 판정보다 낮출 수 없다.
2. **외부와 연동하지 않는다.** 호스팅 서비스 API를 포함해 외부 서비스와 연결하지 않는다. 원격 통신은 사용자가 설정한 git 원격 저장소와 사내 정책 저장소뿐이다.
3. **규칙은 데이터이다.** 규칙 파일로 명령을 실행할 수 없다.
4. **실패해도 멈추지 않는다.** 정책 파일에 문제가 있으면 그 파일만 무시하고 기본 판정으로 동작한다.
5. **관리는 기존 도구에 맡긴다.** 정책 수정 권한은 사내 git 서버의 권한과 서버의 파일 권한으로 정하고, 기록 수집은 회사가 쓰는 배포·수집 도구로 한다. CliGate는 별도 키, 계정, 수집 서버를 두지 않는다.

## 2. 규칙 형식

TOML 파일이고, 정책 하나에 규칙 여러 개가 들어간다.

```toml
[policy]
name = "payments-ops"
version = 13

[audit]
min_level = 2
policy_hits = true
full_command = false

[[rule]]
id = "freeze-main-push"
command = "git push"
when = { branch = ["main"] }
level = 3
word = "freeze"
message = "연말 배포 동결 기간입니다. 긴급 배포는 운영팀 승인 후 진행하세요"
owner = "운영팀 #ops-oncall"
expires = "2027-01-05"
tests = [
  { command = "git push origin main", expect = 3 },
  { command = "git push origin feature/x", expect = 0 },
]
```

### 규칙 필드

| 필드 | 필수 | 뜻 |
| --- | --- | --- |
| `id` | 예 | 규칙 이름. 화면과 기록에 표시 |
| `command` | 예 | 명령과 하위 명령 (`rm`, `git push`, `terraform destroy`) |
| `level` | 예 | 1~3 |
| `message` | 예 | 경고에 보일 설명 |
| `args` | | 인자 중 하나라도 맞으면 해당 (와일드카드) |
| `path` | | 대상 경로. 실제 경로로 바꾼 뒤 비교 |
| `when` | | 상태 조건 |
| `word` | | L3에서 입력할 단어 |
| `suggest` | | 대신 쓸 명령 |
| `owner` | | 담당자 연락처 |
| `mode` | | `observe`(기본) 또는 `enforce` |
| `expires` | | 이 날짜가 지나면 꺼짐 |
| `tests` | | 시험 예시 (명령, 기대 등급) |

### `when` 조건

정해진 목록만 허용한다.

| 종류 | 조건 |
| --- | --- |
| git 상태 | `branch`, `remote`, `forced`, `remote_ahead` |
| 대상 규모 | `untracked`, `count`, `bytes` |
| 환경 | `host`, `cwd`, `user`, `group` |

### 명령 해석

`sudo`, `sh -c`, 별칭 안의 명령도 지금 판정과 같은 방식으로 풀어서 대조한다. CliGate가 기본으로 판정하지 않는 명령(`terraform`, `dd` 등)도 `command`와 `args`로 막을 수 있다.

## 3. 모드

| | `enforce` | `observe` |
| --- | --- | --- |
| 규칙 대조 | 함 | 함 |
| 최종 등급, 화면, 자동완성 배지 | 반영 | 반영 안 함. 사용자에게 알리지 않음 |
| 기록 | 감사 기록 | 관찰 기록 |

- 파일에 쓴 규칙은 `mode`가 없으면 `observe`로 시작한다.
- `policy add`로 추가한 개인 규칙은 `enforce`로 저장한다.
- 미리 알리고 싶은 규칙은 `level = 1`인 `enforce` 규칙으로 배포한다.

## 4. 파일 위치와 합치기

| 우선순위 | 위치 | 관리 | 켜지는 조건 |
| --- | --- | --- | --- |
| 1 | `/etc/cligate/policy.d/*.toml` | 서버 관리자 | root 소유이고 다른 사람이 쓸 수 없을 때 |
| 2 | 저장소의 `.cligate/policy.toml` | 프로젝트 팀 | 사용자가 `trust`로 승인했을 때 |
| 3 | `~/.config/cligate/policy.toml` | 개인 | 항상 |

**합치는 규칙**

- **등급:** 기본 판정과 `enforce` 규칙 중 가장 높은 값
- **경고:** 기본 판정의 근거가 먼저, 그다음 걸린 규칙의 메시지
- **입력 단어:** 걸린 L3 규칙 중 우선순위가 가장 높은 것
- **같은 `id`:** 출처를 붙여 따로 취급 (`시스템/freeze-main-push`)

## 5. 권한

| 역할 | 하는 일 |
| --- | --- |
| 팀 정책 관리자 | 자기 팀 정책 파일의 변경을 승인 |
| 편집자 | 정책 변경을 머지 요청으로 제안 |
| 서버 관리자 | 사내 정책 저장소의 정책을 서버에 반영, 관찰 기록 수집 |
| 일반 사용자 | 개인 규칙 추가와 삭제 (올리기만) |

1. **수정 (사내 git 서버):** 정책 저장소의 기본 브랜치를 보호하고, 작성자가 아닌 사람의 승인을 받아야 합친다. 팀별 담당자 지정(CODEOWNERS)으로 팀 파일은 그 팀 관리자만 승인한다.
2. **반영 (서버):** 정책 저장소 주소는 root만 쓸 수 있는 `/etc/cligate/config.toml`에 둔다. `sync`는 이 주소에서만 받는다.
3. **적용 (CliGate):** `/etc/cligate` 아래 파일이 root 소유가 아니거나 다른 사람이 쓸 수 있으면 읽지 않는다.

## 6. 규칙 추가와 삭제

### 개인 규칙

```bash
cligate policy add --command "terraform destroy" --level 3 --message "운영 인프라를 지웁니다"
cligate policy remove terraform-destroy-1
```

| 명령 | 동작 |
| --- | --- |
| `add` | 입력으로 규칙을 만들어 검사 후 개인 파일 끝에 추가. `enforce`로 저장하고 결과 예시를 `explain`으로 보여줌. `id`가 없으면 자동 생성 |
| `remove` | 규칙을 보여주고 `y/N` 확인 후 삭제. `--yes`로 확인 생략. 팀·프로젝트 규칙은 지우지 않고 어디서 지울지 안내. 직접 고친 파일에서 경계를 정확히 못 찾으면 지우지 않고 편집을 안내 |

`add` 옵션: `--command`, `--level`, `--message`(필수), `--args`, `--path`, `--branch`, `--host`, `--cwd`, `--word`, `--suggest`, `--expires`, `--id`, `--observe`

### 팀 규칙

```
작성 → 검사 → 시험 → 리뷰 → 배포 → 관찰 → 적용
```

1. **작성:** 사내 정책 저장소의 팀 파일을 편집한다.
2. **검사:** `cligate policy check`로 문법과 값을 확인한다.
3. **시험:** `cligate policy test`로 규칙의 예시를 돌리고, `cligate policy explain "명령"`으로 걸리는 규칙과 최종 등급을 본다.
4. **리뷰:** 머지 요청을 올리고 담당자 승인 후 합친다.
5. **배포:** 각 서버에서 `cligate policy sync`로 받는다. `check`를 통과한 파일만 반영한다. 기존 배포 도구로 파일을 복사해도 된다.
6. **관찰:** `observe`로 시작한다. 관리자가 각 서버의 관찰 기록을 모아 `cligate policy hits`로 합쳐 본다.
7. **적용:** 걸린 횟수가 적당하면 `mode = "enforce"`로 바꿔 다시 배포한다.

### 프로젝트 규칙

저장소의 `.cligate/policy.toml`에 적고 커밋한다. 받은 사람은 `cligate policy trust`로 승인한다.

### `check`가 잡는 것

- 모르는 필드와 조건 이름
- 범위를 벗어난 `level`, 중복 `id`
- 잘못된 와일드카드
- 등급을 낮추려는 규칙
- 지난 `expires`
- 지금 적용 중인 것보다 작은 `version`

### `explain` 출력 예

```
$ cligate policy explain "git push origin main"
기본 판정          L2  다른 브랜치에서 main 을 올림
freeze-main-push   L3  연말 배포 동결 기간입니다 (관찰 모드라 지금은 기록만)
최종               L2
```

## 7. 버전과 되돌리기

- `version`은 커지기만 한다. 작거나 같은 버전은 적용하지 않는다.
- 되돌릴 때는 옛 내용을 새 버전 번호로 다시 배포한다.
- 각 서버는 직전 버전 하나를 보관한다 (`policy show --previous`).

## 8. 저장소 규칙의 신뢰

- 처음 보거나 바뀐 저장소 규칙은 `cligate policy trust`로 승인해야 켜진다.
- 저장소 규칙의 `suggest`에는 "저장소 규칙의 제안"이라고 표시한다.

## 9. 기록

### 9.1 기록 형식

관찰 기록과 감사 기록은 같은 형식이다. 한 줄에 사건 하나인 JSON(JSONL)이다.

```json
{"id": "web03-1762480931-4f2a", "time": "2026-11-07T14:02:11+09:00", "host": "web03", "user": "kim", "cwd": "/srv/pay", "source": "system", "policy": "payments-ops", "version": 13, "rule": "freeze-main-push", "mode": "observe", "level": 3, "command": "git push", "result": "passed"}
```

| 필드 | 뜻 |
| --- | --- |
| `id` | 서버 이름, 시각, 임의값으로 만든 고유값. 합칠 때 중복 제거 |
| `time`, `host`, `user`, `cwd` | 언제, 어느 서버에서, 누가, 어디서 |
| `source`, `policy`, `version`, `rule` | 어느 정책의 어떤 규칙 |
| `mode`, `level` | 모드와 등급 |
| `command` | 명령 이름과 옵션. 인자는 `full_command = true`일 때만 |
| `result` | `passed`(관찰 통과), `approved`, `cancelled`, `wrong_word` |

### 9.2 관찰 기록

| 위치 | 대상 | 단계 |
| --- | --- | --- |
| `~/.local/state/cligate/observe.jsonl` | 개인·저장소 규칙의 `observe` 걸림 | 1 |
| `/var/log/cligate/observe.jsonl` | 시스템 정책의 `observe` 걸림 | 2 |

### 9.3 감사 기록 (3단계)

| 대상 | 기록 |
| --- | --- |
| L2 이상인 명령 | 승인, 취소, 잘못된 입력 |
| 팀 규칙에 걸린 명령 | 등급 무관 |
| 판정을 끝내지 못한 명령 | 원격 확인 실패 등 |
| 정책 변경 | 적용과 거부, 정책 저장소의 커밋 번호와 작성자 |

저장 위치는 `/var/log/cligate/audit.jsonl` 또는 사내 syslog이다.

### 9.4 여러 서버의 기록 합치기 (2단계)

**모으기 (CliGate 밖):** 관리자가 평소 쓰는 도구로 각 서버의 기록 파일을 한곳에 가져온다.

```bash
for h in web01 web02 web03; do scp $h:/var/log/cligate/observe.jsonl logs/$h.jsonl; done
ansible web -m fetch -a "src=/var/log/cligate/observe.jsonl dest=logs/"
```

**합쳐 보기**

```
$ cligate policy hits logs/*.jsonl --since 7d
기간 11-01 ~ 11-07   파일 10개   기록 1,284줄   중복 0   읽지 못한 줄 2

규칙                          버전   걸린 횟수   서버   사용자   최근
payments/freeze-main-push     13     41          6      9        오늘 14:02
payments/prod-data-delete     13     0           -      -        -
```

| 옵션 | 뜻 |
| --- | --- |
| (파일 없음) | 지금 서버의 기록만 읽음 (1단계) |
| 파일 여러 개 | 모두 합쳐서 셈 (2단계) |
| `--since` | 기간 |
| `--rule` | 규칙 하나만 |
| `--by-host`, `--by-user` | 서버별, 사용자별로 나눠 보기 |

**처리 방식**

1. 파일을 한 줄씩 읽는다. `.gz` 압축 파일도 읽는다.
2. JSON이 아니거나 필수 필드가 없는 줄은 건너뛰고 개수만 센다. 기간 밖의 기록도 건너뛴다.
3. 이미 본 `id`는 다시 세지 않는다.
4. (정책, 버전, 규칙)별로 횟수, 서버 수, 사용자 수, 최근 시각을 센다.
5. 걸린 횟수 순으로 보여주고, 정책에 있는데 한 번도 안 걸린 규칙은 0으로 보여준다.

표준 라이브러리(`json`, `gzip`, `collections`)만 쓴다. 3단계의 주간 보고서(`cligate audit report`)도 같은 코드를 쓴다.

### 9.5 자동완성 기록과 분리

`~/.local/state/cligate/history`는 개인 자동완성용이라 그대로 두고 위 기록과 섞지 않는다.

## 10. 화면

- **경고 상자:** `[L3] 팀 규칙 freeze-main-push: 연말 배포 동결 기간입니다`, 그 아래 담당자
- **상태 표시줄:** `정책 v13`. 문제가 있으면 `정책 확인 실패`
- **자동완성 배지:** `enforce` 규칙을 반영

## 11. 관리 명령

| 명령 | 용도 | 단계 |
| --- | --- | --- |
| `policy add` | 개인 규칙 추가 | 1 |
| `policy remove ID` | 개인 규칙 삭제 | 1 |
| `policy show` | 적용 중인 규칙과 버전 | 1 |
| `policy check 파일` | 문법과 내용 검사 | 1 |
| `policy test 파일` | 규칙의 `tests` 실행 | 1 |
| `policy explain "명령"` | 걸리는 규칙과 최종 등급 | 1 |
| `policy hits` | 지금 서버의 관찰 기록 집계 | 1 |
| `policy trust` | 저장소 규칙 승인 | 1 |
| `policy sync` | 사내 정책 저장소에서 받아 반영 | 2 |
| `policy hits 파일들` | 여러 서버의 관찰 기록 합산 | 2 |
| `audit report 파일들` | 감사 기록 주간 보고서 | 3 |

## 12. 코드 구조

| 위치 | 변경 |
| --- | --- |
| `policy.py` (새 파일) | 읽기, 파일 권한 확인, 규칙 대조, 모드 처리. 파일이 바뀌면 다시 읽음 |
| `gate.check()` | 끝에 `enforce` 규칙 결과를 더하고, `observe` 걸림은 기록만 |
| `gate.preview()` | 같은 경로라 자동완성 배지에 자동 반영 |
| `audit.py` (새 파일) | 기록 쓰기, 읽기와 합산 (`hits`, `report`) |
| `cli.py` | `policy`, `audit` 하위 명령 |
| `statusline.py` | 정책 버전과 오류 표시 |

**의존성:** Python 3.11 미만에서 TOML 읽기에 `tomli`. 그 밖에 새 의존성은 없다.

## 13. 범위 밖

- 사용자가 CliGate를 쓰지 않거나 `exit`로 나간 경우
- 강제적인 접근 통제. 필요하면 서버 접근통제 솔루션과 함께 쓴다.
- 서버 root 권한자의 변경
- 정책 저장소의 권한 관리. 사내 git 서버 설정이 맡는다.
- 서버에서 기록 파일을 가져오는 일. 회사의 배포·수집 도구가 맡는다.
- 호스팅 서비스 설정(보호 브랜치, 푸시 규칙). 읽지 않고, 보호 여부와 관계없이 지금 판정을 그대로 쓴다.

## 14. 구현 단계

| 단계 | 내용 |
| --- | --- |
| 1 | 규칙 형식, 모드, 개인·저장소 규칙, `add`, `remove`, `show`, `check`, `test`, `explain`, `hits`(지금 서버), `trust`, 개인 관찰 기록, 측정 케이스 |
| 2 | 시스템 정책(`/etc`), 파일 권한 확인, `sync`, 서버 관찰 기록, `hits` 여러 파일 합산 |
| 3 | 감사 기록, `audit report` 주간 보고서 |
| 4 | 대화형 작성기, 서명 |

**1단계 안의 순서:** 시연에 꼭 필요한 것부터 한다.

1. 규칙 형식, `check`, `explain`, 개인 규칙과 판정 연결
2. `add`, `remove`, `show`
3. 모드와 관찰 기록, `hits`
4. 저장소 규칙과 `trust`
5. `test`, 측정 케이스

## 15. 수익화 구상 (발표용)

구현과 관계없는 계획이다. 모든 기능은 공개 코드로 제공하고, 수익은 서비스에서 낸다.

| 수익원 | 설명 |
| --- | --- |
| 폐쇄망 설치·유지보수 계약 | 인터넷이 없는 곳에 설치, 업데이트 반입, 보안성 검토 자료 제공 |
| 정책 컨설팅 | 회사 환경에 맞는 팀 규칙 작성, 관찰 기록 분석 |
| 기술 지원 구독 | 장애 대응, 정기 업데이트 |
| 향후 기업 기능 | 새 기업용 기능을 별도 라이선스로 제공 (오픈 코어로 전환) |
