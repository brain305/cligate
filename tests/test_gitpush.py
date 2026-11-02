import stat
import subprocess
import time

import pytest
from conftest import commit, git, judge, level, messages

from cligate import state
from cligate.risk import L0, L1, L2, L3


@pytest.mark.parametrize("line", [
    "git push --force",
    "git push -f",
    "git push -f origin feat",
    "git push origin +feat",
    "git push -fu origin feat",
    "git -C . push -f",
    "cd . && git push -f",
    "sudo git push -f",
    "git push -f origin HEAD",
    "git push -f origin @",
    "git push -f origin HEAD:feat",
    "git push -f 2>&1",
    "git push -f > out.log",
    "git push -f origin feat 2>&1",
])
def test_force_push_shows_commits_that_disappear(pushed, line):
    verdict = judge(line)
    assert verdict.level == L3
    assert verdict.word == "force"
    text = messages(line)
    assert "커밋 2개가 사라집니다" in text
    assert "team1" in text
    assert "--force-with-lease" in text


@pytest.mark.parametrize("line", [
    "git push --force-with-lease",
    "git push --force-with-lease=feat",
    "git push --dry-run",
    "git push -n",
    "git push --tags",
    "git push origin :",
    "git push -f origin feat:brandnew",
    "git push origin --delete nosuch",
    "git push --delete",
    "git status",
    "git log -f",
    "git fetch -f",
])
def test_not_dangerous(pushed, line):
    assert level(line) == L0


@pytest.mark.parametrize("line, expected", [
    ("git push --all -f", L3),
    ("git push -f origin :", L3),
    ("git push -f nosuchremote feat", L2),
    ("git push -f nosuchremote main", L3),
    ("git push -f $R feat", L2),
    ("git push -f $R main", L3),
])
def test_other_force_cases(pushed, line, expected):
    assert level(line) == expected


class TestUnpulled:
    @pytest.mark.parametrize("line", [
        "git push",
        "git push origin",
        "git push origin feat",
        "git push -u origin feat",
        "git push origin HEAD",
        "git push origin feat:feat",
        "git push origin HEAD:feat",
        "git push 2>&1",
    ])
    def test_asks_and_shows_commits(self, pushed, line):
        verdict = judge(line)
        assert verdict.level == L2
        text = messages(line)
        assert "받지 않은 커밋 2개" in text
        assert "team1" in text and "team2" in text
        assert "git pull --rebase" in text
        assert "--force-with-lease" not in text  # 받으라고 권해야지 덮어쓰라고 권하면 안 된다

    def test_after_pulling_passes(self, pushed):
        git("pull", "-q")
        assert level("git push") == L0

    def test_found_even_when_tracking_info_is_fresh(self, pushed):
        git("fetch", "-q")
        assert level("git push") == L2

    def test_local_commits_too(self, pushed):
        commit(pushed, "mine2")
        assert level("git push") == L2

    def test_up_to_date_passes(self, pushed):
        git("checkout", "-q", "main")
        assert level("git push") == L0

    def test_only_ahead_passes(self, pushed):
        git("checkout", "-q", "main")
        commit(pushed, "local")
        assert level("git push") == L0

    def test_new_branch_passes(self, pushed):
        git("checkout", "-q", "-b", "fresh")
        assert level("git push -u origin fresh") == L0

    def test_no_upstream_lets_git_explain(self, pushed):
        git("checkout", "-q", "-b", "fresh")
        assert level("git push") == L0

    def test_unreachable_remote_lets_git_explain(self, pushed):
        git("remote", "set-url", "origin", "/nosuch/remote.git")
        assert level("git push") == L0

    def test_other_branch_judged_by_its_own_commits(self, pushed):
        git("branch", "-f", "behind-main", "main")
        git("config", "branch.behind-main.remote", "origin")
        assert level("git push origin feat") == L2

    def test_preview_uses_no_network(self, pushed, monkeypatch):
        from cligate import gate, state

        def fail(*args, **kwargs):
            pytest.fail("네트워크 호출")
        monkeypatch.setattr(state, "_git_network", fail)
        assert gate.preview("git push").level == L0
        assert gate.preview("git push --tags").level == L0

    def test_options_not_marked_while_typing(self, pushed):
        from cligate import complete
        git("fetch", "-q")
        found = {c.value: c.meta for c in complete.candidates("git push --", str(pushed), curated=True)}
        assert complete.MARK not in found["--tags"]
        assert complete.MARK not in found["--set-upstream"]

    def test_judging_changes_nothing(self, pushed):
        before = git("rev-parse", "origin/feat", "HEAD")
        judge("git push")
        assert git("rev-parse", "origin/feat", "HEAD") == before

    def test_remote_really_rejects_this_case(self, pushed):
        with pytest.raises(subprocess.CalledProcessError) as caught:
            git("push", "origin", "feat")
        assert "rejected" in caught.value.stderr


def test_force_push_when_same_changes_nothing(pushed):
    git("fetch", "-q")
    git("reset", "-q", "--hard", "origin/feat")
    assert level("git push -f") == L1
    assert "바뀌는 것이 없습니다" in messages("git push -f")


def test_force_push_ahead_only_loses_nothing(pushed):
    git("fetch", "-q")
    git("reset", "-q", "--hard", "origin/feat")
    commit(pushed, "local")
    assert level("git push -f") == L1
    assert "사라지는 커밋은 없습니다" in messages("git push -f")


class TestPushingOtherBranch:
    def test_protected_asks(self, pushed):
        assert level("git push origin main") == L2
        text = messages("git push origin main")
        assert "지금 브랜치는 feat 인데 main 를 올립니다" in text

    @pytest.mark.parametrize("line", ["git push origin master"])
    def test_master_is_protected_too(self, pushed, line):
        git("branch", "master", "main")
        assert level(line) == L2

    def test_other_branch_notifies(self, pushed):
        assert level("git push origin mine") == L1
        assert "지금 브랜치는 feat" in messages("git push origin mine")

    def test_current_branch_into_protected_asks(self, pushed):
        git("fetch", "-q")
        git("reset", "-q", "--hard", "origin/feat")
        assert level("git push origin HEAD:main") == L2
        assert "feat 를 원격 main 로" in messages("git push origin HEAD:main")

    def test_current_branch_into_other_name_notifies(self, pushed):
        git("fetch", "-q")
        git("reset", "-q", "--hard", "origin/feat")
        assert level("git push origin feat:elsewhere") == L1

    def test_on_main_pushing_main_is_quiet(self, pushed):
        git("checkout", "-q", "main")
        assert level("git push origin main") == L0
        assert level("git push") == L0

    def test_current_branch_is_quiet(self, pushed):
        git("fetch", "-q")
        git("reset", "-q", "--hard", "origin/feat")
        assert level("git push origin feat") == L0
        assert level("git push") == L0

    def test_tag_is_quiet(self, pushed):
        git("fetch", "-q")
        git("reset", "-q", "--hard", "origin/feat")
        git("tag", "v1")
        assert level("git push origin v1") == L0

    def test_unknown_name_lets_git_explain(self, pushed):
        git("fetch", "-q")
        git("reset", "-q", "--hard", "origin/feat")
        assert level("git push origin nosuch") == L0

    def test_detached_is_quiet(self, pushed):
        git("checkout", "-q", "--detach", "main")
        assert level("git push origin main") == L0

    def test_dry_run_is_quiet(self, pushed):
        assert level("git push -n origin main") == L0

    def test_preview_is_quiet(self, pushed):
        from cligate import gate
        assert gate.preview("git push origin main").level == L0

    def test_highest_with_unpulled(self, pushed):
        verdict = judge("git push origin mine feat")
        assert verdict.level == L2
        assert "받지 않은 커밋" in messages("git push origin mine feat")
        assert "지금 브랜치는 feat" in messages("git push origin mine feat")


class TestProtectedBranch:
    def test_same_as_remote_changes_nothing(self, pushed):
        assert level("git push -f origin main") == L1
        assert "바뀌는 것이 없습니다" in messages("git push -f origin main")

    def test_really_changing_needs_word(self, pushed):
        git("checkout", "-q", "main")
        commit(pushed, "hotfix")
        verdict = judge("git push -f origin main")
        assert verdict.level == L3
        assert verdict.word == "force"
        assert "보호 브랜치 main" in messages("git push -f origin main")

    def test_behind_remote_shows_lost_commits(self, pushed):
        git("checkout", "-q", "main")
        git("reset", "-q", "--hard", "HEAD~1")
        assert level("git push -f origin main") == L3
        assert "사라집니다" in messages("git push -f origin main")


def test_rebased_own_commit_does_not_count(pushed):
    git("checkout", "-q", "mine")
    git("checkout", "-q", "main")
    commit(pushed, "main2")
    git("checkout", "-q", "mine")
    git("rebase", "-q", "main")
    assert level("git push -f") == L1


def test_judging_leaves_tracking_ref_alone(pushed):
    before = git("rev-parse", "origin/feat")
    judge("git push --force")
    judge("git push origin --delete feat")
    assert git("rev-parse", "origin/feat") == before


def test_force_with_lease_still_protects_after_judging(pushed):
    judge("git push --force")
    commit(pushed, "mine2")
    with pytest.raises(Exception) as caught:
        git("push", "--force-with-lease", "origin", "feat")
    assert "stale info" in caught.value.stderr


class TestDelete:
    @pytest.mark.parametrize("line", [
        "git push origin --delete merged",
        "git push origin :merged",
        "git push -d origin merged",
        "git push --delete origin merged",
        "git push -f origin :merged",
        "git push origin --delete merged 2>&1",
    ])
    def test_kept_on_another_remote_branch(self, pushed, line):
        assert level(line) == L1
        assert "다른 원격 브랜치에 남아" in messages(line)

    @pytest.mark.parametrize("line", ["git push origin --delete mine", "git push origin :mine", "git push -f origin :mine"])
    def test_kept_only_on_this_computer(self, pushed, line):
        assert level(line) == L2
        assert "이 컴퓨터에만" in messages(line)

    @pytest.mark.parametrize("line", ["git push origin --delete gone", "git push origin +:gone"])
    def test_kept_nowhere(self, pushed, line):
        verdict = judge(line)
        assert verdict.level == L3
        assert verdict.word == "force"
        assert "어디에도 남지 않습니다" in messages(line)

    def test_unfetched_team_commits_count_as_lost(self, pushed):
        assert level("git push origin --delete feat") == L3
        assert "team1" in messages("git push origin --delete feat")

    @pytest.mark.parametrize("line", ["git push origin --delete main", "git push origin :main"])
    def test_protected(self, pushed, line):
        assert level(line) == L3

    @pytest.mark.parametrize("line, expected", [
        ("git push origin --delete $B", L2),
        ("git push nosuchremote --delete feat", L2),
        ("git push nosuchremote --delete main", L3),
    ])
    def test_unverifiable(self, pushed, line, expected):
        assert level(line) == expected

    def test_tag(self, pushed):
        git("tag", "v1")
        git("push", "-q", "origin", "v1")
        assert level("git push origin --delete v1") == L2
        assert level("git push origin :refs/tags/v1") == L2

    def test_mixed_with_plain_push(self, pushed):
        assert level("git push origin main :gone") == L3
        assert "main" not in messages("git push origin main :gone").split("\n")[0]

    def test_several_take_the_highest(self, pushed):
        assert level("git push origin --delete merged gone") == L3


def _script(path, body):
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def test_hanging_remote_is_cut_off(pushed, tmp_path, monkeypatch):
    git("remote", "add", "slow", "ssh://nobody@example.invalid/x.git")
    monkeypatch.setattr(state, "NETWORK_TIMEOUT", 1)
    monkeypatch.setenv("GIT_SSH_COMMAND", _script(tmp_path / "hang", "sleep 30"))
    started = time.time()
    assert level("git push -f slow feat") == L2
    assert time.time() - started < 5


def test_own_ssh_setting_is_respected(pushed, tmp_path):
    log = tmp_path / "ssh.log"
    git("remote", "add", "slow", "ssh://nobody@example.invalid/x.git")
    git("config", "core.sshCommand", _script(tmp_path / "myssh", f"echo called >> {log}; exit 1"))
    judge("git push -f slow feat")
    assert log.read_text().startswith("called")
