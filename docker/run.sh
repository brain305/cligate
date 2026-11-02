#!/bin/sh
# 맥에서 실행한다. 사용법:
#   docker/run.sh          시연 환경 (녹화용)
#   docker/run.sh test     리눅스에서 전체 테스트
#   docker/run.sh shell    시연 상태에서 셸 열기 (cligate 없이 확인할 때)
#
# 폴더 연결(-v), --privileged, docker.sock 연결은 일부러 쓰지 않는다. 쓰면 컨테이너 안의 rm 이
# 맥의 파일에 닿을 수 있다. --network none 이라 실제 GitHub 에도 닿지 않는다.
set -e
cd "$(dirname "$0")/.."

case "${1:-demo}" in
    demo)
        docker build -q --target demo -t cligate-demo . >/dev/null
        exec docker run --rm -it --network none --hostname cligate-demo cligate-demo
        ;;
    shell)
        docker build -q --target demo -t cligate-demo . >/dev/null
        exec docker run --rm -it --network none --hostname cligate-demo cligate-demo /bin/zsh
        ;;
    test)
        docker build -q --target test -t cligate-test . >/dev/null
        exec docker run --rm --network none --hostname cligate-test cligate-test
        ;;
    *)
        echo "사용법: docker/run.sh [demo|test|shell]" >&2
        exit 2
        ;;
esac
