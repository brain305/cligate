# cligate

2026 capstone design
https://claude.ai/artifact/DCdcEaPgrq86FK64MXn2NF


## 파일 역할
| 파일 | 역할 |
| --- | --- |
| `cli.py` | 진입점. 입력 루프 |
| `parse.py` | 명령 분리(`&&`, `\|`, `;`), 문법 검사 |
| `builtin.py` | `cd`, `exit`, `export`, `unset` — 셸에 넘기면 상태가 안 남는 명령들 |
| `gate.py` | 위험 명령 판별 |


## 진행 현황
| 단계 | 내용 | 상태 |
| --- | --- | --- |
| 1 | 설치 배선, `cligate` 명령어 | ✅ |
| 2 | 입력 루프, 셸 위임, 빌트인 | ✅ |
| 3 | 명령 파싱, 문법 검사 | ✅ |
| 4 | 위험 판정 | 🚧 `rm` 판별만 |
| 5 | git 상태 수집 | ⬜ |
| 6 | 확인·차단 게이트 | ⬜ |
| 7 | 0.5초 멈추면 뜨는 자동완성 | ⬜ |



## 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cligate
```

