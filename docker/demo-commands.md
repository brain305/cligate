# 시연 명령 목록

`docker/run.sh`로 시작합니다. 저장소는 `~/project`이고 브랜치는 `feature/login`입니다.
모든 명령은 이 순서대로 실제로 돌려 등급을 확인했습니다. 위에서부터 입력하면 됩니다.

`[취소]`는 엔터를 치라는 뜻입니다. `[틀린 단어]`는 `y`를 입력합니다. **`delete`와 `force`는 입력하지 마세요.**

입력창 바로 아래에 원격 상태가 표시됩니다. 처음에는 `↓2 pull 필요  ·  ✎2 add 필요  ·  ●1 커밋 대기`처럼 나옵니다. 자동완성 목록이 뜨면 그 자리를 잠시 씁니다.

## L0: 위험하지 않으면 그대로 실행
| # | 입력 | 보여줄 점 |
|---|---|---|
| 1 | `ls` | 평소와 같음 |
| 2 | `git status` | 스테이지됨, 수정됨, 미추적 파일이 모두 보임 |
| 3 | `git log --oneline -3` | 평소와 같음 |
| 4 | `echo 추가 >> notes.txt` | 덧붙이기는 잃을 것이 없음 |
| 5 | `cp README.md README-copy.md` | 덮어쓸 파일이 없음 |

## L1: 되돌릴 수 있으면 알리고 실행
| # | 입력 | 보여줄 점 |
|---|---|---|
| 6 | `rm logs/app.log` | 커밋되어 있어 복구 안내 |
| 7 | `git restore logs/app.log` | 실제로 되살아남 |
| 8 | `$(echo ls) -a` | 무슨 명령인지 몰라도 막지 않고 알림 |
| 9 | `git push origin --delete merged-old` | 커밋이 다른 브랜치에 남음 |

## L2: 되돌릴 수 없으면 근거를 보여주고 확인
| # | 입력 | 보여줄 점 |
|---|---|---|
| 10 | `rm notes.txt` `[취소]` | 6번과 같은 `rm`인데 반응이 다름 |
| 11 | `echo 새내용 > notes.txt` `[취소]` | 조용히 덮어쓰는 것을 막음 |
| 12 | `cp README.md notes.txt` `[취소]` | 복사로 덮어쓰기 |
| 13 | `git reset --hard` `[취소]` | 잃을 파일 이름 표시 |
| 14 | `git checkout .` `[취소]` | 같은 손실을 다른 명령으로 |
| 15 | `git clean -fd` `[취소]` | 지울 파일을 미리 표시 |
| 16 | `git branch -D experiment` `[취소]` | 사라질 커밋과 복구 명령 |
| 17 | `git stash drop` `[취소]` | 지울 항목 표시 |
| 18 | `git switch -f main` `[취소]` | 버려질 변경 표시 |
| 19 | `git push origin --delete solo` `[취소]` | 9번과 같은 명령인데 반응이 다름 |

## L3: 가장 위험하면 단어를 직접 입력해야 실행
| # | 입력 | 보여줄 점 |
|---|---|---|
| 20 | `sudo rm -rf /` `[틀린 단어]` | `y`로는 지워지지 않음 |
| 21 | `rm -rf ~` `[틀린 단어]` | 홈 폴더 |
| 22 | `rm -rf $BUILD/` `[틀린 단어]` | 변수가 비어 `/`가 되는 경우 |
| 23 | `rm -rf .git` `[틀린 단어]` | 커밋 기록 전체 |
| 24 | `rm build/old-*.log` `[틀린 단어]` | 파일이 많음 (60개) |
| 25 | `rm build/dump.sql` `[틀린 단어]` | 파일이 큼 (120MB) |
| 26 | `git clean -fdx` `[틀린 단어]` | 15번에 `-x`만 붙어도 규모가 커짐 |
| 27 | `rmf /` `[틀린 단어]` | 별칭 안에 숨은 `rm -rf` |
| 28 | `git push --force` `[틀린 단어]` | 사라질 팀원 커밋 표시 |
| 29 | `git push -f origin main` `[틀린 단어]` | 보호 브랜치 |

## 자동완성: 입력한 뒤 엔터 없이 잠시 기다림
다 보고 나면 `Ctrl+U`로 줄을 지웁니다.
| # | 입력 | 보여줄 점 |
|---|---|---|
| 30 | `git pu` | 설명과 함께 후보 표시 |
| 31 | `git add ` | `.`과 바뀐 파일이 먼저 |
| 32 | `git reset ` | `--hard ⚠`가 맨 아래 |
| 33 | `rm ` | 복구 불가 파일이 맨 아래, `⚠` |
| 34 | `cd ` | 폴더만 표시 |
| 35 | `git fetch` (엔터) | 팀원 커밋을 받음 |
| 36 | `git push --` | `--force ⚠ L3`가 맨 아래 |
| 37 | `git pu` | `pull`이 위로 |

## 원격 상태 표시
입력창 아래 줄을 봅니다. 위 명령들을 모두 마친 뒤 마지막에 합니다.
| # | 입력 | 보여줄 점 |
|---|---|---|
| 38 | `git commit -m 시험` | 커밋은 되지만 갈라진다고 미리 알림 |
| 39 | `git push` `[취소]` | 받지 않은 팀원 커밋을 보여주고 `git pull --rebase` 권장 |
| 40 | `git push origin main` `[취소]` | 다른 브랜치에서 `main`을 올리려 하면 멈춤 |
| 41 | `git switch main` | 브랜치가 바뀌면 상태 줄도 `↑1 push 대기`로 바뀜 |

## 상태에 따라 바뀌는 자동완성과 알림
| # | 입력 | 보여줄 점 |
|---|---|---|
| 42 | `git ` (엔터 없이 대기) | 상태 줄의 알림 순서대로 `add`, `push`가 맨 위. 본 뒤 `Ctrl+U` |
| 43 | `git switch feature/login` | 원래 브랜치로 돌아옴 |
| 44 | `cd src` | 하위 폴더로 이동 |
| 45 | `git add .` | 이 폴더 아래만 추가되고 바깥의 `notes.txt`가 남는다고 알림 |
| 46 | `git ` (엔터 없이 대기) | 받을 커밋이 있어 `pull --rebase`가 맨 위. 본 뒤 `Ctrl+U` |
| 47 | `cd ..` | 맨 위 폴더로 돌아옴 |

## 팀 규칙 (v0.3.0)
개인 규칙으로 CliGate가 기본으로 판정하지 않는 명령을 막는다.

| # | 입력 | 보여줄 점 |
|---|---|---|
| 48 | `cligate policy add --command "terraform destroy" --level 3 --message "운영 인프라를 지웁니다"` | 한 줄로 규칙 추가. 예시 판정 L3 |
| 49 | `terraform destroy` `[취소]` | 기본 판정에는 없던 명령이 `[L3] 개인 규칙`으로 막힘. 엔터로 취소 |
| 50 | `cligate policy explain "terraform destroy"` | 기본 판정 L0, 규칙 L3, 최종 L3 |
| 51 | `cligate policy remove terraform-destroy-1` → `y` | 확인을 받고 지움 |

사내 정책 저장소에서 팀 규칙을 받아 적용하고, 감사 기록을 본다.

| # | 입력 | 보여줄 점 |
|---|---|---|
| 52 | `sudo cligate policy sync` | 정책 저장소에서 `ops.toml v1`을 받아 추가 |
| 53 | `cligate policy show` | 팀 규칙 적용 중. `no-dropdb`는 적용, `watch-dd`는 관찰 모드 |
| 54 | `dropdb payments` `[취소]` | `[L3] 팀 규칙 no-dropdb`와 담당 DBA팀 표시. 입력 단어는 `dropdb`. 엔터로 취소 |
| 55 | `dd if=/dev/zero of=/dev/null count=1` | 관찰 모드라 경고 없이 실행 |
| 56 | `cligate policy hits` | 관찰 모드 규칙 `watch-dd`가 1번 걸림 |
| 57 | `rm notes.txt` `[취소]` | 확인 화면에서 취소. 감사 기록에 남음 |
| 58 | `sudo cligate audit report --since 1d` | 등급별 취소와 멈춘 비율, 팀 규칙별 통계, 정책 변경 기록 |
