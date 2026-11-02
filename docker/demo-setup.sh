#!/bin/sh
# 녹화에 쓸 저장소 상태를 만든다. 실행할 때마다 처음 상태로 돌아간다.
#   ~/remote.git  원격 저장소 (네트워크 없이 같은 컨테이너 안에 있다)
#   ~/teammate    팀원의 작업 공간. 내가 아직 받지 않은 커밋 2개를 올려 둔다
#   ~/project     내 작업 공간. 여기서 시연한다
set -e
cd "$HOME"
rm -rf remote.git teammate project

# 별칭 안에 숨은 위험을 보여주기 위한 설정
cat > "$HOME/.zshrc" <<'RC'
alias rmf='rm -rf'
alias gs='git status'
RC

git init -q --bare remote.git
git clone -q remote.git project 2>/dev/null
cd project
git checkout -q -B main

mkdir -p src logs
echo 'print("hello")' > src/app.py
echo '시작 로그' > logs/app.log
echo '오류 로그' > logs/error.log
echo '# 시연용 프로젝트' > README.md
printf '*.tmp\nbuild/\n' > .gitignore
git add -A
git commit -qm "프로젝트 시작"
git config core.pager cat
git push -q -u origin main

# 병합이 끝난 브랜치: 원격에서 지워도 커밋이 main 에 남는다
git branch merged-old
git push -q origin merged-old

# 원격에 올렸고 내 컴퓨터에도 있는 브랜치: 원격에서 지우면 이 컴퓨터에만 남는다
git checkout -q -b solo
echo '혼자 하던 일' > solo.txt
git add -A
git commit -qm "혼자 하던 작업"
git push -q -u origin solo
git checkout -q main

git checkout -q -b feature/login
echo 'def login(): pass' > src/login.py
git add -A
git commit -qm "feat: 로그인 추가"
git push -q -u origin feature/login

# 올리지 않은 브랜치: 지우면 커밋이 어디에도 남지 않는다
git checkout -q -b experiment
echo '실험' > experiment.txt
git add -A
git commit -qm "아무 데도 안 올린 실험"
git checkout -q feature/login

# 치워둔 변경
echo '# 임시' >> README.md
git stash push -q -m "작업 중이던 README"

cd "$HOME"
git clone -q remote.git teammate 2>/dev/null
cd teammate
git config user.name "팀원"
git config user.email "teammate@example.com"
git checkout -q feature/login
echo 'def test_login(): pass' > src/test_login.py
git add -A
git commit -qm "test: 로그인 테스트 추가"
echo 'def login(): return True' > src/login.py
git add -A
git commit -qm "fix: 로그인 리다이렉트 수정"
git push -q origin feature/login

# 아직 fetch 하지 않았으므로 내 쪽에서는 팀원의 커밋이 보이지 않는다
cd "$HOME/project"
echo '아직 커밋하지 않은 메모' > notes.txt
echo 'print("수정 중")' >> src/app.py
echo 'DEBUG = True' > src/config.py
git add src/config.py  # 스테이지된 파일: git status 에 세 종류가 모두 보인다

# git 이 무시하는 대량 파일과 큰 파일: 규모에 따라 등급이 오르는 것을 보여준다
mkdir -p build
for i in $(seq 1 60); do echo "로그 $i" > "build/old-$i.log"; done
truncate -s 120M build/dump.sql

if [ $# -gt 0 ]; then
    exec "$@"
fi

cat <<'EOF'
────────────────────────────────────────────────
 CliGate 시연 환경 (Docker)  저장소: ~/project
 맥의 파일은 보이지 않습니다. 나가면 모두 사라집니다.
 끝내려면 exit
────────────────────────────────────────────────
EOF
exec cligate
