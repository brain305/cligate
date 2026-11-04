# 측정 케이스의 상태를 만드는 함수. 시험용 임시 폴더 안에서만 쓴다
export GIT_AUTHOR_NAME=me GIT_AUTHOR_EMAIL=me@test GIT_COMMITTER_NAME=me GIT_COMMITTER_EMAIL=me@test

make_fixture() {  # $1 아래에 원격, 팀원, 작업 저장소를 만들고 작업 저장소로 들어간다
    git init -q --bare -b main "$1/origin.git"
    git clone -q "$1/origin.git" "$1/team" 2>/dev/null
    git clone -q "$1/origin.git" "$1/work" 2>/dev/null
    cd "$1/work" || return 1
    git switch -q -c main 2>/dev/null
    mkdir -p src logs
    echo a > a.txt; echo b > b.txt; echo app > src/app.py; echo log > logs/app.log
    git add . && git commit -q -m 처음 && git push -q -u origin main
}

on_feature() { git switch -q -c feature/login && git push -q -u origin feature/login; }

local_commit() { echo "$RANDOM" >> a.txt; git add a.txt; git commit -q -m 로컬; }

teammate() {  # 팀원이 브랜치에 커밋 $1 개를 올린다. 작업 저장소는 아직 모른다
    ( cd ../team && git fetch -q origin && git switch -q -C "$2" "origin/$2" &&
      for i in $(seq "$1"); do echo "$i" >> team.txt; git add team.txt; git commit -q -m "팀원 $i"; done &&
      git push -q origin "$2" )
}

teammate_new_branch() {  # 작업 저장소에 없는 원격 브랜치를 팀원이 만든다
    ( cd ../team && git fetch -q origin && git switch -q -c "$1" origin/main &&
      echo "$1" > "$1.txt" && git add . && git commit -q -m "$1" && git push -q origin "$1" )
}

logs() {  # 커밋된 로그 파일 $1 개를 만든다
    for i in $(seq "$1"); do echo log > "logs/old-$i.log"; done
    git add logs && git commit -q -m 로그
}

sparse() { dd bs=1048576 count=0 seek="$2" of="$1" 2>/dev/null; }  # 디스크를 쓰지 않는 $2 MB 파일

merge_conflict() {
    git switch -q -c other && echo 저쪽 > a.txt && git commit -qam 저쪽
    git switch -q main && echo 이쪽 > a.txt && git commit -qam 이쪽
    git merge other >/dev/null 2>&1 || true
}

rebase_conflict() {
    git switch -q -c other && echo 저쪽 > a.txt && git commit -qam 저쪽
    git switch -q main && echo 이쪽 > a.txt && git commit -qam 이쪽
    git switch -q other; git rebase main >/dev/null 2>&1 || true
}
