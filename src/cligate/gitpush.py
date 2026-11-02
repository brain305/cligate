from . import parse, risk, state
from .risk import Finding, L1, L2, L3, R1, R2, R3, S2

CONFIRM_WORD = "force"
PROTECTED = {"main", "master"}
SAFER = "    권장  git push --force-with-lease --force-if-includes  (원격이 바뀌었으면 거부됩니다)"
OFFLINE_NOTE = "    (마지막 fetch 기준의 추정입니다. 실행할 때 원격을 다시 확인합니다)"

PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def check(args, cwd, offline=False):
    """git push 의 인자를 판정한다. 원격은 공유 자원이므로 영향 범위는 언제나 S2 이다.

    offline 이면 원격에 묻지 않고 마지막 fetch 로 남은 추적 정보만 본다. 원격이 그 뒤로 바뀌었을 수
    있으므로 "잃을 것이 없다" 고 단정하지 않고 L2 이상으로 올린다. 실행할 때는 원격을 다시 확인한다.
    """
    findings = _check(args, cwd, offline)
    if offline:
        for finding in findings:
            finding.level = max(finding.level, L2)
            finding.message += f"\n{OFFLINE_NOTE}"
    return findings


def _check(args, cwd, offline):
    forced, deleting, positional = _read_push(parse.strip_redirects(args))
    deleting_spec = any(_is_delete(s) for s in positional[1:])
    if not (forced or deleting or deleting_spec or any(p.startswith("+") for p in positional)):
        return []

    if not deleting and any(o in args for o in ("--all", "--mirror", "--branches")):
        return [Finding(L3, f"git push: 여러 브랜치를 강제로 덮어씁니다\n{SAFER}", CONFIRM_WORD)]
    if any("$" in p for p in positional):
        protected = any(p.lstrip("+").rpartition(":")[2] in PROTECTED for p in positional)
        level = L3 if protected else L2
        if deleting or deleting_spec:
            return [Finding(level, "git push: 어떤 원격 브랜치를 지우는지 확인할 수 없습니다", CONFIRM_WORD)]
        return [Finding(level, f"git push: 어느 원격에 보내는지 확인할 수 없습니다\n{SAFER}", CONFIRM_WORD)]

    remote = positional[0] if positional else None
    specs = positional[1:]
    if deleting:
        if not specs:
            return []  # 지울 이름이 없으면 git 이 알아서 거부한다
        specs = [":" + s.lstrip("+") for s in specs]
    elif not specs:
        current = state.current_branch(cwd)
        if current is None:
            return []  # 저장소 밖이거나 브랜치가 없으면 git 이 알아서 거부한다
        specs = [current]
        remote = remote or state.push_remote(current, cwd)

    findings = []
    overwrites = []
    for raw in specs:
        spec = raw.lstrip("+")
        src, _, dst = spec.partition(":")
        if _is_delete(spec):
            found = _check_delete(remote, dst, cwd, offline)
        elif forced or raw.startswith("+"):
            found = _check_ref(remote, src, dst or src, cwd, offline)
            overwrites.extend(f for f in found if f.level >= L2)
        else:
            continue  # git push origin feat :old 의 feat 는 평범한 푸시다
        findings.extend(found)
    if overwrites:
        overwrites[-1].message += f"\n{SAFER}"
    return findings


def _is_delete(spec):
    return spec.lstrip("+").startswith(":") and len(spec.lstrip("+")) > 1  # : 만 쓰면 삭제가 아니다


def _remote_sha(remote, branch, cwd, offline):
    return state.tracked_head(remote, branch, cwd) if offline else state.remote_head(remote, branch, cwd)


def _check_delete(remote, dst, cwd, offline):
    """원격 브랜치 삭제를 판정한다. 기준은 지운 뒤 그 커밋이 어디에 남는가이다."""
    dst = dst.removeprefix("refs/heads/")
    target = f"{remote}/{dst}"
    if dst.startswith("refs/tags/"):
        return [risk.judge(R2, S2, f"git push: 원격 태그 {dst.removeprefix('refs/tags/')} 삭제", CONFIRM_WORD)]

    protected = dst in PROTECTED
    remote_sha = _remote_sha(remote, dst, cwd, offline)
    if remote_sha is None:
        message = f"git push: 원격 브랜치 {target} 삭제. 상태를 확인하지 못했습니다"
        return [Finding(L3 if protected else L2, message, CONFIRM_WORD)]
    if remote_sha == "":
        if state.remote_tag(remote, dst, cwd):
            return [risk.judge(R2, S2, f"git push: 원격 태그 {dst} 삭제", CONFIRM_WORD)]
        return []  # 원격에 없으면 git 이 알아서 거부한다
    if protected:
        return [Finding(L3, f"git push: 보호 브랜치 {target} 삭제", CONFIRM_WORD)]

    result = state.deleted_commits(remote, dst, remote_sha, cwd)
    if result is None:
        return [Finding(L3, f"git push: 원격 브랜치 {target} 삭제. 커밋을 확인하지 못했습니다", CONFIRM_WORD)]
    lost, local_only = result
    if lost:
        title = f"git push: 원격 브랜치 {target} 삭제. 커밋 {len(lost)}개가 어디에도 남지 않습니다"
        return [risk.judge(R3, S2, _list_commits(title, lost), CONFIRM_WORD)]
    if local_only:
        title = f"git push: 원격 브랜치 {target} 삭제. 커밋 {len(local_only)}개가 이 컴퓨터에만 남습니다"
        return [risk.judge(R2, S2, _list_commits(title, local_only), CONFIRM_WORD)]
    message = f"git push: 원격 브랜치 {target} 삭제. 커밋은 다른 원격 브랜치에 남아 있습니다"
    return [risk.judge(R1, S2, message, CONFIRM_WORD)]


def _check_ref(remote, src, dst, cwd, offline):
    if not src:  # : 만 쓰면 같은 이름 브랜치를 모두 보낸다
        return [Finding(L3, "git push: 여러 브랜치를 강제로 덮어씁니다", CONFIRM_WORD)]
    if dst in ("HEAD", "@"):
        dst = state.current_branch(cwd) or dst  # git push -f origin HEAD 는 지금 브랜치로 보낸다
    dst = dst.removeprefix("refs/heads/")
    protected = dst in PROTECTED
    remote_sha = _remote_sha(remote, dst, cwd, offline)
    if remote_sha is None:
        message = f"git push --force: 원격 {remote}/{dst} 을 확인하지 못했습니다"
        return [Finding(L3 if protected else L2, message, CONFIRM_WORD)]
    if remote_sha == "":
        return []  # 원격에 없는 새 브랜치라 덮어쓸 것이 없다

    commits = state.remote_only_commits(remote, dst, src, remote_sha, cwd)
    if commits is None:
        return [Finding(L3, f"git push --force: 원격 {remote}/{dst} 의 커밋을 확인하지 못했습니다", CONFIRM_WORD)]
    if commits:
        title = f"git push --force: {remote}/{dst} 의 커밋 {len(commits)}개가 사라집니다"
        return [risk.judge(R3, S2, _list_commits(title, commits), CONFIRM_WORD)]
    if protected:
        return [Finding(L3, f"git push --force: 보호 브랜치 {dst} 를 강제로 덮어씁니다", CONFIRM_WORD)]
    return [Finding(L1, f"git push --force: {remote}/{dst} 에서 사라지는 커밋은 없습니다", CONFIRM_WORD)]


def _list_commits(title, commits):
    lines = [title]
    lines += [f"    {commit}" for commit in commits[:risk.MAX_LISTED]]
    if len(commits) > risk.MAX_LISTED:
        lines.append(f"    ... 외 {len(commits) - risk.MAX_LISTED}개")
    return "\n".join(lines)


def _read_push(args):
    forced = False
    deleting = False
    positional = []
    skip_next = False
    for word in args:
        if skip_next:
            skip_next = False
        elif word in PUSH_VALUE_OPTS:
            skip_next = True
        elif word.startswith("--"):
            forced = forced or word == "--force"
            deleting = deleting or word == "--delete"
        elif word.startswith("-"):
            forced = forced or "f" in word
            deleting = deleting or "d" in word
        else:
            positional.append(word)
    return forced, deleting, positional
