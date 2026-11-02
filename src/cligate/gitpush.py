from . import parse, state

CONFIRM_WORD = "force"
PROTECTED = {"main", "master"}
MAX_LISTED = 8
SAFER = "    권장  git push --force-with-lease --force-if-includes  (원격이 바뀌었으면 거부됩니다)"

GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
PUSH_VALUE_OPTS = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}


def check(real, cwd):
    """git push 를 판정한다. (알릴 문구, 확인받을 문구, 입력할 단어) 를 돌려준다. 해당 없으면 모두 None."""
    args, cwd = _push_args(real, cwd)
    if args is None:
        return None, None, None
    forced, deleting, positional = _read_push(parse.strip_redirects(args))
    deleting_spec = any(_is_delete(s) for s in positional[1:])
    if not (forced or deleting or deleting_spec or any(p.startswith("+") for p in positional)):
        return None, None, None

    if not deleting and any(o in args for o in ("--all", "--mirror", "--branches")):
        return None, f"git push: 여러 브랜치를 강제로 덮어씁니다\n{SAFER}", CONFIRM_WORD
    if any("$" in p for p in positional):
        protected = any(p.lstrip("+").rpartition(":")[2] in PROTECTED for p in positional)
        word = CONFIRM_WORD if protected else None
        if deleting or deleting_spec:
            return None, "git push: 어떤 원격 브랜치를 지우는지 확인할 수 없습니다", word
        return None, f"git push: 어느 원격에 보내는지 확인할 수 없습니다\n{SAFER}", word

    remote = positional[0] if positional else None
    specs = positional[1:]
    if deleting:
        if not specs:
            return None, None, None  # 지울 이름이 없으면 git 이 알아서 거부한다
        specs = [":" + s.lstrip("+") for s in specs]
    elif not specs:
        current = state.current_branch(cwd)
        if current is None:
            return None, None, None  # 저장소 밖이거나 브랜치가 없으면 git 이 알아서 거부한다
        specs = [current]
        remote = remote or state.push_remote(current, cwd)

    notices, warnings, word = [], [], None
    overwrites = False
    for raw in specs:
        spec = raw.lstrip("+")
        src, _, dst = spec.partition(":")
        if _is_delete(spec):
            notice, warning, spec_word = _check_delete(remote, dst, cwd)
        elif forced or raw.startswith("+"):
            notice, warning, spec_word = _check_ref(remote, src, dst or src, cwd)
            overwrites = overwrites or bool(warning)
        else:
            continue  # git push origin feat :old 의 feat 는 평범한 푸시다
        if notice:
            notices.append(notice)
        if warning:
            warnings.append(warning)
        word = word or spec_word
    if overwrites:
        warnings.append(SAFER)
    return "\n".join(notices) or None, "\n".join(warnings) or None, word


def _is_delete(spec):
    return spec.lstrip("+").startswith(":") and len(spec.lstrip("+")) > 1  # : 만 쓰면 삭제가 아니다


def _check_delete(remote, dst, cwd):
    """원격 브랜치 삭제를 판정한다. 기준은 지운 뒤 그 커밋이 어디에 남는가이다."""
    dst = dst.removeprefix("refs/heads/")
    target = f"{remote}/{dst}"
    if dst.startswith("refs/tags/"):
        return None, f"git push: 원격 태그 {dst.removeprefix('refs/tags/')} 삭제", None

    protected = dst in PROTECTED
    remote_sha = state.remote_head(remote, dst, cwd)
    if remote_sha is None:
        return None, f"git push: 원격 브랜치 {target} 삭제. 상태를 확인하지 못했습니다", CONFIRM_WORD if protected else None
    if remote_sha == "":
        if state.remote_tag(remote, dst, cwd):
            return None, f"git push: 원격 태그 {dst} 삭제", None
        return None, None, None  # 원격에 없으면 git 이 알아서 거부한다
    if protected:
        return None, f"git push: 보호 브랜치 {target} 삭제", CONFIRM_WORD

    result = state.deleted_commits(remote, dst, remote_sha, cwd)
    if result is None:
        return None, f"git push: 원격 브랜치 {target} 삭제. 커밋을 확인하지 못했습니다", CONFIRM_WORD
    lost, local_only = result
    if lost:
        return None, _list_commits(f"git push: 원격 브랜치 {target} 삭제. 커밋 {len(lost)}개가 어디에도 남지 않습니다", lost), CONFIRM_WORD
    if local_only:
        return None, _list_commits(f"git push: 원격 브랜치 {target} 삭제. 커밋 {len(local_only)}개가 이 컴퓨터에만 남습니다", local_only), None
    return f"git push: 원격 브랜치 {target} 삭제. 커밋은 다른 원격 브랜치에 남아 있습니다", None, None


def _check_ref(remote, src, dst, cwd):
    if not src:  # : 만 쓰면 같은 이름 브랜치를 모두 보낸다
        return None, "git push: 여러 브랜치를 강제로 덮어씁니다", CONFIRM_WORD
    if dst in ("HEAD", "@"):
        dst = state.current_branch(cwd) or dst  # git push -f origin HEAD 는 지금 브랜치로 보낸다
    dst = dst.removeprefix("refs/heads/")
    protected = dst in PROTECTED
    remote_sha = state.remote_head(remote, dst, cwd)
    if remote_sha is None:
        message = f"git push --force: 원격 {remote}/{dst} 을 확인하지 못했습니다"
        return None, message, CONFIRM_WORD if protected else None
    if remote_sha == "":
        return None, None, None  # 원격에 없는 새 브랜치라 덮어쓸 것이 없다

    commits = state.remote_only_commits(remote, dst, src, remote_sha, cwd)
    if commits is None:
        return None, f"git push --force: 원격 {remote}/{dst} 의 커밋을 확인하지 못했습니다", CONFIRM_WORD
    if commits:
        return None, _list_commits(f"git push --force: {remote}/{dst} 의 커밋 {len(commits)}개가 사라집니다", commits), CONFIRM_WORD
    if protected:
        return None, f"git push --force: 보호 브랜치 {dst} 를 강제로 덮어씁니다", CONFIRM_WORD
    return f"git push --force: {remote}/{dst} 에서 사라지는 커밋은 없습니다", None, None


def _list_commits(title, commits):
    lines = [title]
    lines += [f"    {commit}" for commit in commits[:MAX_LISTED]]
    if len(commits) > MAX_LISTED:
        lines.append(f"    ... 외 {len(commits) - MAX_LISTED}개")
    return "\n".join(lines)


def _push_args(real, cwd):
    """git -C x push ... 에서 push 뒤의 인자와 실제 위치를 꺼낸다. push 가 아니면 (None, cwd)."""
    i = 1
    while i < len(real) and real[i].startswith("-"):
        if real[i] == "-C" and i + 1 < len(real):
            cwd = state.join(cwd, real[i + 1])
        i += 2 if real[i] in GIT_VALUE_OPTS else 1
    if i >= len(real) or real[i] != "push":
        return None, cwd
    return real[i + 1:], cwd


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
