# CliGate 시험과 시연용 환경. 위험한 명령을 맥이나 실제 저장소가 아닌 곳에서 돌리기 위해 쓴다.
# 코드는 이미지 안에 복사하고 폴더를 연결하지 않으므로, 컨테이너에는 맥의 파일이 보이지 않는다.
ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim AS base
# 슬림 이미지는 번역 파일을 빼고 설치하므로 그 설정을 지운다
RUN sed -i '/usr.share.locale/d' /etc/dpkg/dpkg.cfg.d/* \
    && apt-get update && apt-get install -y --no-install-recommends git zsh sudo vim less locales \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i 's/^# *ko_KR.UTF-8/ko_KR.UTF-8/' /etc/locale.gen && locale-gen
RUN useradd --create-home --shell /bin/zsh demo \
    && echo "demo ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/demo
COPY pyproject.toml /app/
COPY src /app/src
RUN pip install --no-cache-dir /app
# 커밋 시각과 git 안내 문구를 한국어로 보여준다
ENV SHELL=/bin/zsh LANG=ko_KR.UTF-8
USER demo
WORKDIR /home/demo
RUN touch /home/demo/.zshrc \
    && git config --global user.name "나" \
    && git config --global user.email "me@example.com" \
    && git config --global init.defaultBranch main \
    && git config --global advice.detachedHead false

# 시연: 녹화할 상태를 만들고 cligate 를 띄운다. 나가면 컨테이너와 함께 사라진다
FROM base AS demo
COPY --chmod=755 docker/demo-setup.sh /usr/local/bin/demo-setup
ENTRYPOINT ["/usr/local/bin/demo-setup"]

# 시험: 리눅스에서 전체 테스트를 돌린다
FROM base AS test
USER root
RUN pip install --no-cache-dir "pytest>=8"
COPY tests /app/tests
RUN chown -R demo /app/tests
USER demo
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

# 측정: 보고서 지표 케이스를 돌린다. 명령은 판정만 하고 실행하지 않는다
FROM base AS eval
USER root
RUN pip install --no-cache-dir pyyaml
COPY eval /app/eval
USER demo
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "eval/run.py"]
