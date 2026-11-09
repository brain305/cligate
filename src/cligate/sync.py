"""cligate policy sync. 사내 정책 저장소에서 팀 규칙을 받아 /etc/cligate/policy.d 에 반영한다.

저장소 주소는 root 만 쓸 수 있는 /etc/cligate/config.toml 에만 둔다. 받은 파일은 policy check 를
통과하고 버전이 지금보다 커야 반영한다. 바꾸기 전의 파일은 previous 에 하나 보관한다.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from . import audit, policy

CLONE_SECONDS = 60
SYNC_KEYS = {"repo", "branch", "dir"}
LOG_DIR_MODE = 0o1733  # 누구나 자기 파일을 만들 수 있지만 남의 파일은 지우거나 볼 수 없다


def config_path():
    return os.path.join(policy.etc_dir(), "config.toml")


def previous_dir():
    return os.path.join(policy.etc_dir(), "previous")


def manifest_path():
    return os.path.join(policy.etc_dir(), "synced.json")


class SyncError(Exception):
    pass


def read_config():
    path = config_path()
    if not os.path.exists(path):
        raise SyncError(f"{path} 가 없습니다. [sync] repo 에 사내 정책 저장소 주소를 적으세요")
    reason = policy.unsafe_reason(path)
    if reason:
        raise SyncError(f"설정 파일의 권한이 안전하지 않습니다. {reason}")
    try:
        with open(path, "rb") as f:
            section = tomllib.load(f).get("sync", {})
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise SyncError(f"설정 파일을 읽지 못했습니다: {e}")
    unknown = section.keys() - SYNC_KEYS if isinstance(section, dict) else {"(표가 아님)"}
    if unknown or not isinstance(section.get("repo"), str) or not section["repo"]:
        raise SyncError("설정 파일의 [sync] 에는 repo(필수), branch, dir 만 쓸 수 있습니다")
    return section["repo"], section.get("branch"), section.get("dir", ".")


def sync(say=print):
    """받아서 반영한다. 모두 반영하면 0, 반영하지 못한 파일이 있으면 1."""
    if os.geteuid() != 0 and not os.environ.get(policy.ETC_ENV):
        raise SyncError(f"root 권한이 필요합니다. sudo {policy.APP_NAME} policy sync")
    repo, branch, folder = read_config()
    with tempfile.TemporaryDirectory() as temp:
        checkout = os.path.join(temp, "repo")
        command = ["git", "clone", "--quiet", "--depth", "1", *(["--branch", branch] if branch else []), repo, checkout]
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
        try:
            done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=CLONE_SECONDS)
        except subprocess.TimeoutExpired:
            raise SyncError(f"{CLONE_SECONDS}초 안에 받지 못했습니다: {repo}")
        if done.returncode:
            raise SyncError(f"정책 저장소를 받지 못했습니다: {done.stderr.strip()[-200:]}")
        commit = subprocess.run(["git", "-C", checkout, "log", "-1", "--format=%h %an"],
                                capture_output=True, text=True).stdout.strip()
        source = os.path.normpath(os.path.join(checkout, folder))
        if not source.startswith(checkout) or not os.path.isdir(source):
            raise SyncError(f"저장소에 {folder} 폴더가 없습니다")
        say(f"받음  {repo}  {commit}")
        return _apply(source, say, commit)


def _apply(source, say, commit=""):
    for folder, mode in ((policy.etc_dir(), 0o755), (policy.system_dir(), 0o755), (previous_dir(), 0o755)):
        os.makedirs(folder, mode=mode, exist_ok=True)
    _ensure_log_dir()
    synced = _read_manifest()
    names = sorted(n for n in os.listdir(source) if n.endswith(".toml"))
    refused = 0
    for name in names:
        new = os.path.join(source, name)
        target = os.path.join(policy.system_dir(), name)
        loaded = policy.load(new, policy.SYSTEM, strict=True)
        if loaded.errors:
            refused += 1
            say(f"건너뜀  {name}  오류 {len(loaded.errors)}개: {loaded.errors[0]}")
            audit.record_policy_change("refused", name, after=loaded.version, commit=commit, reason=loaded.errors[0])
            continue
        if os.path.exists(target):
            current = policy.load(target, policy.SYSTEM)
            if current.digest == loaded.digest:
                say(f"그대로  {name}  v{loaded.version}")
                continue
            if loaded.version <= current.version:
                refused += 1
                say(f"건너뜀  {name}  v{loaded.version} 이 적용 중인 v{current.version} 보다 크지 않습니다")
                audit.record_policy_change("refused", name, current.version, loaded.version, commit, "버전이 커지지 않음")
                continue
            shutil.copy2(target, os.path.join(previous_dir(), name))
            say(f"바꿈    {name}  v{current.version} → v{loaded.version}  규칙 {len(loaded.rules)}개")
            audit.record_policy_change("changed", name, current.version, loaded.version, commit)
        else:
            say(f"추가    {name}  v{loaded.version}  규칙 {len(loaded.rules)}개")
            audit.record_policy_change("added", name, after=loaded.version, commit=commit)
        _install(new, target)
        synced.add(name)
    for name in sorted(synced - set(names)):
        target = os.path.join(policy.system_dir(), name)
        if os.path.exists(target):
            before = policy.load(target, policy.SYSTEM).version
            shutil.move(target, os.path.join(previous_dir(), name))
            say(f"지움    {name}  (저장소에서 사라짐. previous 에 보관)")
            audit.record_policy_change("removed", name, before=before, commit=commit)
        synced.discard(name)
    _write_manifest(synced)
    return 1 if refused else 0


def _install(new, target):
    temp = f"{target}.{os.getpid()}"
    shutil.copyfile(new, temp)
    os.chmod(temp, 0o644)
    os.replace(temp, target)


def _ensure_log_dir():
    try:
        os.makedirs(policy.log_dir(), exist_ok=True)
        os.chmod(policy.log_dir(), LOG_DIR_MODE)
    except OSError:
        pass  # 기록 폴더가 없어도 관찰 기록은 사용자 폴더에 남는다


def _read_manifest():
    try:
        with open(manifest_path(), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, ValueError, TypeError):
        return set()


def _write_manifest(names):
    temp = f"{manifest_path()}.{os.getpid()}"
    with open(temp, "w", encoding="utf-8") as f:
        json.dump(sorted(names), f)
    os.chmod(temp, 0o644)
    os.replace(temp, manifest_path())


def previous_versions():
    """보관한 직전 버전들 [(파일 이름, Policy)]."""
    try:
        names = sorted(n for n in os.listdir(previous_dir()) if n.endswith(".toml"))
    except OSError:
        return []
    return [(n, policy.load(os.path.join(previous_dir(), n), policy.SYSTEM)) for n in names]
