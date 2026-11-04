import os
import re
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import commit, git, write

from cligate import complete, gitspec, state
from cligate.complete import candidates


def values(text, cwd):
    return [c.value for c in candidates(text, str(cwd))]


def meta(text, cwd):
    return {c.value: c.meta for c in candidates(text, str(cwd))}


class TestCommands:
    def test_blank_shows_base_order(self, repo):
        assert values("", repo)[:3] == ["cd", "ls", "vim"]

    def test_prefix(self, repo):
        assert values("m", repo)[0] == "mkdir"

    def test_after_separator(self, repo):
        assert values("ls && gi", repo)[0] == "git"

    def test_path_executables_included(self, repo):
        assert "git" in values("g", repo)

    def test_wrapper_is_skipped(self, repo):
        assert "status" in values("sudo git st", repo)


class TestGitSubcommands:
    def test_base_order_on_clean_repo(self, repo):
        names = values("git ", repo)
        assert names[:2] == ["status", "push"]
        assert names.index("pull") < names.index("log") < names.index("rebase")

    def test_clean_repo_lowers_add_commit_stash(self, repo):
        names = values("git ", repo)
        assert names[-3:] == ["add", "commit", "stash"]

    def test_modified_file_raises_add_and_lowers_merge(self, repo):
        write(repo / "a", "changed\n")
        names = values("git ", repo)
        assert names[:2] == ["add", "stash"]
        assert names[-1] == "merge"

    def test_real_commands_from_git(self, repo):
        assert "cherry-pick" in values("git ", repo)

    def test_prefix(self, repo):
        assert values("git pu", repo) == ["push", "pull"]

    def test_behind_raises_pull_and_lowers_push(self, pushed):
        git("fetch", "-q")
        git("checkout", "-q", "feat")
        assert values("git pu", pushed) == ["pull", "push"]

    def test_diverged_recommends_pull_rebase(self, pushed):
        git("fetch", "-q")
        git("checkout", "-q", "feat")
        commit(pushed, "local")
        names = values("git p", pushed)
        assert names[0] == "pull --rebase"
        assert names[-1] == "push"

    def test_detached_recommends_switch_c(self, repo):
        git("checkout", "-q", "--detach")
        assert values("git sw", repo)[0] == "switch -c"

    def test_merge_conflict_raises_add_lowers_commit(self, repo):
        git("checkout", "-q", "-b", "side")
        write(repo / "a", "side\n")
        git("commit", "-qam", "side")
        git("checkout", "-q", "main")
        write(repo / "a", "main\n")
        git("commit", "-qam", "main")
        subprocess.run(["git", "merge", "side"], capture_output=True)
        names = values("git ", repo)
        assert names[:2] == ["add", "mergetool"]
        assert names[-1] == "commit"

    def test_rebase_conflict_recommends_continue(self, repo):
        git("checkout", "-q", "-b", "side")
        write(repo / "a", "side\n")
        git("commit", "-qam", "side")
        git("checkout", "-q", "main")
        write(repo / "a", "main\n")
        git("commit", "-qam", "main")
        git("checkout", "-q", "side")
        subprocess.run(["git", "rebase", "main"], capture_output=True)
        names = values("git ", repo)
        assert names[:3] == ["rebase --continue", "rebase --abort", "add"]
        assert names.index("rebase") > names.index("status")
        assert values("git rebase --c", repo)[0] == "--continue"

    def test_outside_repo_has_no_rules(self, tmp_path):
        assert values("git ", tmp_path)[:2] == ["status", "add"]


class TestGitOptions:
    def test_push_base_order_with_force_at_the_bottom(self, pushed):
        git("checkout", "-q", "main")  # 원격과 같은 브랜치라 상태 규칙이 끼어들지 않는다
        names = values("git push --", pushed)
        assert names[0] == "--set-upstream"
        assert names[-1] == "--force"
        assert "--force-with-lease" in names[:3]

    def test_figure_nine_behind_raises_lease(self, pushed):
        git("checkout", "-q", "feat")
        git("fetch", "-q")
        names = values("git push --", pushed)
        assert names[0] == "--force-with-lease"
        assert names[-1] == "--force"
        assert "L3" in meta("git push --", pushed)["--force"]

    def test_status_line_behind_raises_lease_before_fetch(self, pushed, monkeypatch):
        from cligate import statusline
        git("checkout", "-q", "feat")
        m = statusline.Monitor()
        monkeypatch.setattr(statusline, "_current", m)
        m.check_once()
        assert m.status.behind == 2
        assert values("git push --", pushed)[0] == "--force-with-lease"

    def test_failed_remote_check_keeps_fetched_counts(self, pushed, monkeypatch):
        from cligate import statusline
        git("checkout", "-q", "feat")
        git("fetch", "-q")
        m = statusline.Monitor()
        m.status = statusline.Status(os.path.realpath(pushed), "feat", statusline.FAILED)
        monkeypatch.setattr(statusline, "_current", m)
        assert values("git push --", pushed)[0] == "--force-with-lease"
        assert values("git ", pushed)[0] == "pull"

    def test_never_hidden(self, pushed):
        assert "--force" in values("git push --f", pushed)

    def test_no_negated_options(self, repo):
        assert not [n for n in values("git push --", repo) if n.startswith("--no-") and n != "--no-verify"]

    def test_reset_hard_marked_only_when_there_is_work_to_lose(self, repo):
        assert complete.MARK not in meta("git reset --", repo)["--hard"]
        write(repo / "a", "changed\n")
        assert "L2" in meta("git reset --", repo)["--hard"]
        assert values("git reset --", repo)[-1] == "--hard"

    def test_clean_force_marked(self, repo):
        write(repo / "u")
        assert "L2" in meta("git clean --", repo)["--force"]

    def test_checkout_force_marked(self, repo):
        write(repo / "a", "changed\n")
        assert "L2" in meta("git checkout --", repo)["--force"]

    def test_judging_candidates_executes_nothing(self, repo):
        write(repo / "u")
        write(repo / "a", "changed\n")
        values("git clean --", repo)
        values("git reset --", repo)
        assert (repo / "u").exists()
        assert (repo / "a").read_text() == "changed\n"


class TestGitArguments:
    def test_switch_branches(self, pushed):
        assert "feat" in values("git switch f", pushed)

    def test_push_remote_then_branch(self, pushed):
        assert values("git push o", pushed) == ["origin"]
        assert "feat" in values("git push origin f", pushed)

    def test_checkout_branch_before_files(self, pushed):
        names = values("git checkout ", pushed)
        assert names.index("feat") < names.index("init")

    def test_add_files(self, repo):
        assert "logs/" in values("git add ", repo)


class TestPaths:
    def test_cd_directories_only(self, repo):
        assert values("cd ", repo) == ["d/", "logs/"]

    def test_files_first_for_file_commands(self, repo):
        names = values("cat ", repo)
        assert names.index("c") < names.index("d/")

    def test_alphabetical(self, repo):
        assert values("ls ", repo) == ["a", "b", "c", "d/", "logs/"]

    def test_inside_directory(self, repo):
        assert values("cat logs/", repo) == ["logs/1.log", "logs/2.log"]

    def test_prefix(self, repo):
        assert values("cat logs/1", repo) == ["logs/1.log"]

    def test_hidden_only_when_asked(self, repo):
        assert ".gitignore" not in values("ls ", repo)
        assert ".gitignore" in values("ls .", repo)

    def test_space_is_quoted(self, repo):
        write(repo / "with space")
        assert "'with space'" in values("cat w", repo)

    def test_missing_folder(self, repo):
        assert values("cat nosuch/", repo) == []

    def test_home(self, repo):
        write(Path(os.path.expanduser("~")) / "inhome")
        write(Path(os.path.expanduser("~")) / "in home")
        assert values("cat ~/inh", repo) == ["~/inhome"]
        assert values("cat ~/in\\", repo) == []
        assert "~/'in home'" in values("cat ~/in", repo)

    def test_rm_lost_files_go_last_and_marked(self, repo):
        write(repo / "aa-new")
        write(repo / "a", "changed\n")
        write(repo / "logs" / "u.log")
        names = values("rm ", repo)
        assert names[:3] == ["b", "c", "d/"]
        assert set(names[3:]) == {"a", "aa-new", "logs/"}
        assert "복구 불가" in meta("rm ", repo)["aa-new"]
        assert meta("rm ", repo)["b"] == ""

    def test_rm_outside_repo_has_no_marks(self, tmp_path):
        write(tmp_path / "f")
        assert meta("rm ", tmp_path)["f"] == ""


class TestHalfTyped:
    @pytest.mark.parametrize("text", ['echo "hel', "rm 'a"])
    def test_open_quote_shows_nothing(self, repo, text):
        assert values(text, repo) == []

    @pytest.mark.parametrize("text", ["&&", "| ", "(", "; ", "git -C", "git -C nosuch ", "sudo ", "FOO=1 "])
    def test_does_not_crash(self, repo, text):
        candidates(text, str(repo))


class TestHints:
    @pytest.mark.parametrize("name, hint", [("branch", "<이름>"), ("switch", "<브랜치>"), ("commit", "메시지"), ("reset", "--hard")])
    def test_shown_beside_candidate(self, repo, name, hint):
        assert hint in meta("git ", repo)[name]

    def test_hint_is_not_inserted(self, repo):
        found = next(c for c in candidates("git bra", str(repo)) if c.value == "branch")
        assert found.value + found.append == "branch "

    def test_every_hint_is_for_a_real_command(self, repo):
        names = state.git_commands()
        assert all(name in names for name in gitspec.COMMANDS)

    def test_no_space_for_commands_usually_run_bare(self, repo):
        found = {c.value: c.append for c in candidates("git ", str(repo))}
        assert found["status"] == ""
        assert found["log"] == ""
        assert found["branch"] == " "


class TestFirstArguments:
    def test_commit_message_flag_first(self, repo):
        found = candidates("git commit ", str(repo))
        assert found[0].value == "-m"
        assert found[0].append == " "

    def test_commit_flag_with_dash(self, repo):
        assert values("git commit -", repo)[0] == "-m"

    def test_only_right_after_subcommand(self, repo):
        assert "-m" not in values("git commit a ", repo)

    def test_reset_hard_risky_goes_last_when_work_would_be_lost(self, repo):
        write(repo / "a", "changed\n")
        found = candidates("git reset ", str(repo))
        assert found[0].value == "HEAD~1"
        assert found[-1].value == "--hard"
        assert "L2" in found[-1].meta

    def test_clean_dry_run_before_delete(self, repo):
        write(repo / "u")
        found = candidates("git clean ", str(repo))
        assert found[0].value == "-n"
        assert "L2" in {c.value: c.meta for c in found}["-fd"]

    def test_stash_subcommands(self, repo):
        assert values("git stash ", repo)[:2] == ["pop", "list"]

    def test_first_arguments_do_not_execute(self, repo):
        write(repo / "u")
        candidates("git clean ", str(repo))
        candidates("git reset ", str(repo))
        assert (repo / "u").exists()


class TestRealValues:
    def test_push_without_upstream(self, pushed):
        git("checkout", "-q", "-b", "fresh")
        assert values("git pu", pushed)[0] == "push -u origin fresh"

    def test_push_with_upstream_has_no_full_form(self, pushed):
        git("checkout", "-q", "main")
        assert not [v for v in values("git pu", pushed) if v.startswith("push -u")]

    def test_no_remote_no_full_form(self, repo):
        assert values("git pu", repo) == ["push", "pull"]

    def test_merged_branch_not_listed_before_typing(self, repo):
        git("branch", "done")
        assert not [v for v in values("git ", repo) if v.startswith("branch -d")]

    def test_switch_previous_branch(self, repo):
        git("checkout", "-q", "-b", "side")
        assert values("git sw", repo)[0] == "switch main"
        assert "직전" in meta("git sw", repo)["switch main"]

    def test_no_previous_branch(self, repo):
        assert not [v for v in values("git sw", repo) if " " in v]

    def test_delete_merged_branch(self, repo):
        git("branch", "done")
        git("checkout", "-q", "-b", "other")
        git("checkout", "-q", "main")
        write(repo / "a", "x\n")
        git("commit", "-qam", "ahead")
        git("checkout", "-q", "-b", "open")
        write(repo / "a", "y\n")
        git("commit", "-qam", "open")
        git("checkout", "-q", "main")
        names = values("git bra", repo)
        assert "branch -d done" in names
        assert "branch -d other" in names
        assert "branch -d open" not in names
        assert "branch -d main" not in names

    def test_commit_when_staged(self, repo):
        assert "commit -m" not in values("git com", repo)
        write(repo / "a", "changed\n")
        assert "commit -m" not in values("git com", repo)
        git("add", "a")
        found = {c.value: c for c in candidates("git com", str(repo))}
        assert found["commit -m"].append == " "

    def test_reset_needs_parent(self, tmp_path, monkeypatch):
        git("init", "-q", "-b", "main", cwd=tmp_path)
        commit(tmp_path, "only")
        assert "reset HEAD~1" not in values("git res", tmp_path)
        commit(tmp_path, "two")
        assert "reset HEAD~1" in values("git res", tmp_path)

    def test_stash_pop_only_with_stash(self, repo):
        assert "stash pop" not in values("git st", repo)
        write(repo / "a", "changed\n")
        git("stash", "-q")
        assert "stash pop" in values("git st", repo)

    def test_real_values_run_as_is(self, pushed):
        git("checkout", "-q", "-b", "fresh")
        line = "git " + values("git pu", pushed)[0]
        git(*line.split()[1:])
        assert git("ls-remote", "--heads", "origin", "fresh") != ""


class TestChangedFirst:
    def test_add_lists_changed_then_rest(self, repo):
        write(repo / "z-new")
        write(repo / "c", "changed\n")
        names = values("git add ", repo)
        assert names[:4] == [".", "-p", "c", "z-new"]
        assert "a" in names[4:]
        assert names.count("c") == 1

    def test_restore_skips_untracked(self, repo):
        write(repo / "z-new")
        write(repo / "c", "changed\n")
        assert meta("git restore ", repo)["c"] == "변경됨"
        assert meta("git restore ", repo).get("z-new") == ""

    def test_relative_to_current_folder(self, repo, monkeypatch):
        write(repo / "d" / "x", "changed\n")
        write(repo / "c", "changed\n")
        monkeypatch.chdir(repo / "d")
        assert values("git add ", repo / "d")[2] == "x"
        assert "c" not in values("git add ", repo / "d")


class TestNoEndlessRepeat:
    @pytest.mark.parametrize("text", [
        "git merge main ",
        "git merge main side ",
        "git switch main ",
        "git rebase main ",
        "git branch -d side ",
        "git push origin main ",
        "git push -u origin main ",
        "git pull origin main ",
        "git checkout main ",
        "git stash pop ",
        "git reset HEAD~1 ",
        "git reset --hard ",
        "git switch -c ",
        "git checkout -b ",
        "git commit -m ",
        'git commit -m "hi" ',
        "cd d ",
    ])
    def test_nothing_more_to_suggest(self, repo, text):
        git("branch", "side")
        assert values(text, repo) == []

    def test_still_suggests_before_finished(self, repo):
        git("branch", "side")
        assert "side" in values("git merge ", repo)
        assert "side" in values("git switch s", repo)
        assert "side" in values("git branch -d ", repo)

    def test_finished_word_alone_is_not_suggested(self, repo):
        assert values("git merge main", repo) == []

    def test_options_still_available_after_arguments(self, repo):
        assert "--squash" in values("git merge main --", repo)
        assert "--no-ff" in values("git merge main --no", repo)

    def test_negated_options_only_when_asked(self, repo):
        assert "--no-tags" not in values("git push --", repo)
        assert "--no-tags" in values("git push --no-t", repo)

    def test_pull_branches_then_remote(self, pushed):
        names = values("git pull ", pushed)
        assert "origin feat" in names
        assert names[-1] == "origin"
        assert "feat" in values("git pull origin ", pushed)

    def test_fetch_remote(self, pushed):
        assert values("git fetch ", pushed) == ["origin"]

    def test_files_not_repeated(self, repo):
        assert "a" not in values("git add a ", repo)
        assert "b" in values("git add a ", repo)
        assert "a" not in values("cat a ", repo)
        assert "b" in values("rm a ", repo)

    def test_folder_not_repeated(self, repo):
        assert "d/" not in values("ls d/ ", repo)
        assert "d/" not in values("ls d ", repo)

    def test_after_double_dash_are_files(self, repo):
        git("branch", "side")
        names = values("git checkout -- ", repo)
        assert "a" in names
        assert "side" not in names
        assert "b" in values("git checkout -- a ", repo)


def curated(text, cwd):
    return [c.value for c in candidates(text, str(cwd), curated=True)]


class TestCurated:
    def test_git_add_dot_comes_first(self, repo):
        found = candidates("git add ", str(repo), curated=True)
        assert found[0].value == "."
        assert found[0].meta == "변경 전부"

    def test_git_add_dot_is_final(self, repo):
        assert values("git add . ", repo) == []

    def test_only_common_commands_listed(self, repo):
        names = curated("git ", repo)
        assert "commit" in names
        assert "archimport" not in names
        assert "cat-file" not in names
        assert len(names) <= len(gitspec.COMMANDS)

    def test_tab_still_lists_everything(self, repo):
        assert "cat-file" in values("git ", repo)

    def test_rare_command_found_when_typed(self, repo):
        assert "cat-file" in curated("git cat-", repo)

    def test_common_options_only(self, pushed):
        git("checkout", "-q", "main")
        names = curated("git push --", pushed)
        assert names[-1] == "--force"
        assert len(names) == 5

    def test_rare_option_found_when_typed(self, repo):
        assert "--verbose" in curated("git push --ver", repo)

    def test_force_is_visible_with_description(self, pushed):
        git("checkout", "-q", "main")
        commit(pushed, "hotfix")  # 원격과 달라야 보호 브랜치를 실제로 바꾼다
        force = candidates("git push --", str(pushed), curated=True)[-1]
        assert "L3" in force.meta
        assert "덮어씀" in force.meta

    def test_every_listed_command_has_description(self, repo):
        assert all(c.meta for c in candidates("git ", str(repo), curated=True))

    def test_common_shell_commands_only(self, repo):
        assert curated("v", repo) == ["vim"]
        assert set(curated("v", repo)) < set(values("v", repo)) or values("v", repo) == ["vim"]

    def test_rare_shell_command_found_when_typed(self, repo):
        assert "git" in curated("gi", repo)


class TestPull:
    def test_upstream_first_with_count_after_fetch(self, pushed):
        git("fetch", "-q")
        found = candidates("git pull ", str(pushed))
        assert found[0].value == "origin feat"
        assert found[0].meta == "↓2 받을 커밋"

    def test_others_sorted_by_count_and_explain_merge(self, pushed):
        git("fetch", "-q")
        found = {c.value: c.meta for c in candidates("git pull ", str(pushed))}
        assert found["origin mine"] == "↓1 받을 커밋 · 지금 브랜치에 합침"
        assert found["origin main"] == "받을 커밋 없음"

    def test_order(self, pushed):
        git("fetch", "-q")
        names = values("git pull ", pushed)
        assert names.index("origin feat") < names.index("origin mine") < names.index("origin main") < names.index("origin")

    def test_no_head_entry(self, pushed):
        assert "origin HEAD" not in values("git pull ", pushed)

    def test_uses_fresher_status_line(self, pushed, monkeypatch):
        from cligate import statusline
        m = statusline.Monitor()
        monkeypatch.setattr(statusline, "_current", m)
        assert candidates("git pull ", str(pushed))[0].meta == "받을 커밋 없음"  # 아직 fetch 하지 않았다
        m.check_once()
        assert candidates("git pull ", str(pushed))[0].meta == "↓2 받을 커밋"

    def test_typing_branch_name_filters(self, pushed):
        assert values("git pull fe", pushed) == ["origin feat"]

    def test_typing_remote_name_filters(self, pushed):
        names = values("git pull or", pushed)
        assert names[0] == "origin feat"
        assert names[-1] == "origin"

    def test_second_position_shows_branch_only(self, pushed):
        git("fetch", "-q")
        found = candidates("git pull origin ", str(pushed))
        assert found[0].value == "feat"
        assert found[0].meta == "↓2 받을 커밋"

    def test_finished_after_branch(self, pushed):
        assert values("git pull origin feat ", pushed) == []

    def test_with_rebase_option(self, pushed):
        assert "origin feat" in values("git pull --rebase ", pushed)

    def test_options_still_work(self, pushed):
        assert "--rebase" in values("git pull --", pushed)

    def test_completion_inserted_runs(self, pushed):
        git("fetch", "-q")
        value = candidates("git pull ", str(pushed))[0].value
        git("pull", "-q", *value.split())
        assert git("rev-list", "--count", "HEAD..origin/feat") == "0"

    def test_no_remote(self, repo):
        assert values("git pull ", repo) == []

    def test_outside_repo(self, tmp_path):
        assert values("git pull ", tmp_path) == []

    def test_no_network(self, pushed, monkeypatch):
        def fail(*args, **kwargs):
            pytest.fail("네트워크 호출")
        monkeypatch.setattr(state, "_git_network", fail)
        candidates("git pull ", str(pushed))

    def test_many_branches_are_capped(self, pushed):
        for i in range(40):
            git("update-ref", f"refs/remotes/origin/b{i}", "HEAD")
        assert len(values("git pull origin ", pushed)) <= state.MAX_PULL_BRANCHES


class TestStatusDrivesOrder:
    """상태 줄에 뜬 알림이 git 목록의 맨 위가 된다. 순서는 받기, add, commit, push 이다."""

    def first(self, cwd):
        return values("git ", cwd)[:3]

    @pytest.fixture
    def synced(self, pushed):
        git("checkout", "-q", "main")
        return pushed

    def test_only_push_waiting(self, synced):
        commit(synced, "local")
        assert self.first(synced)[0] == "push"

    def test_only_pull_needed(self, pushed):
        git("fetch", "-q")
        assert self.first(pushed)[0] == "pull"
        assert values("git ", pushed).index("push") > values("git ", pushed).index("log")

    def test_only_add_needed(self, synced):
        write(synced / "new", "x\n")
        assert self.first(synced)[0] == "add"

    def test_only_commit_waiting(self, synced):
        write(synced / "new", "x\n")
        git("add", "new")
        assert self.first(synced)[:2] == ["commit", "stash"]

    def test_add_then_commit(self, synced):
        write(synced / "new", "x\n")
        git("add", "new")
        write(synced / "other", "x\n")
        assert self.first(synced)[:2] == ["add", "commit"]

    def test_add_commit_push_in_order(self, synced):
        commit(synced, "local")
        write(synced / "new", "x\n")
        git("add", "new")
        write(synced / "other", "x\n")
        assert self.first(synced) == ["add", "commit", "push"]

    def test_pull_comes_before_add(self, pushed):
        git("fetch", "-q")
        write(pushed / "new", "x\n")
        assert self.first(pushed)[:2] == ["pull", "add"]

    def test_diverged_rebase_first(self, pushed):
        git("fetch", "-q")
        commit(pushed, "local")
        names = values("git ", pushed)
        assert names[0] == "pull --rebase"
        assert names[-1] == "add" or names.index("push") > names.index("log")

    def test_clean_and_synced_keeps_base_order(self, synced):
        assert self.first(synced)[0] == "status"

    def test_unpublished_branch_raises_command_that_works(self, synced):
        git("checkout", "-q", "-b", "fresh")
        found = candidates("git ", str(synced))[0]
        assert found.value == "push -u origin fresh"
        assert found.meta == "upstream 설정"
        git("push", "-q", "-u", "origin", "fresh")
        assert git("ls-remote", "--heads", "origin", "fresh") != ""

    def test_no_remote_does_not_raise_push(self, repo):
        assert self.first(repo)[0] == "status"

    def test_conflict_beats_everything(self, repo):
        git("checkout", "-q", "-b", "side")
        write(repo / "a", "side\n")
        git("commit", "-qam", "side")
        git("checkout", "-q", "main")
        write(repo / "a", "main\n")
        git("commit", "-qam", "main")
        subprocess.run(["git", "merge", "side"], capture_output=True)
        write(repo / "new", "x\n")
        assert self.first(repo)[:2] == ["add", "mergetool"]

    def test_status_line_value_is_used(self, pushed, monkeypatch):
        from cligate import statusline
        m = statusline.Monitor()
        monkeypatch.setattr(statusline, "_current", m)
        m.check_once()  # 아직 fetch 하지 않아 추적 정보로는 모른다
        assert self.first(pushed)[0] == "pull"

    def test_learning_does_not_outrank_status(self, synced):
        from cligate import learn, parse
        commit(synced, "local")
        for _ in range(50):
            learn.record(parse.parse("git log"))
        assert self.first(synced)[0] == "push"

    def test_typed_prefix_respected(self, synced):
        commit(synced, "local")
        assert values("git s", synced)[0] != "push"
        assert values("git pu", synced)[0] == "push"


class TestStashMark:
    def test_drop_marked_only_when_there_is_something(self, repo):
        assert complete.MARK not in meta("git stash ", repo)["drop"]
        write(repo / "a", "changed\n")
        git("stash", "-q")
        assert "L2" in meta("git stash ", repo)["drop"]
        assert values("git stash ", repo)[-1] == "drop"


class TestSnapshot:
    def test_clean(self, repo):
        snap = state.snapshot(str(repo))
        assert not (snap.changed or snap.untracked or snap.conflicts or snap.detached or snap.rebasing)

    def test_outside_repo(self, tmp_path):
        assert state.snapshot(str(tmp_path)) is None

    def test_ahead_behind(self, pushed):
        git("fetch", "-q")
        git("checkout", "-q", "feat")
        commit(pushed, "local")
        snap = state.snapshot(str(pushed))
        assert (snap.ahead, snap.behind) == (1, 2)


class _Event:
    def __init__(self, requested):
        self.completion_requested = requested


class _Document:
    def __init__(self, text):
        self.text_before_cursor = text


class TestBudget:
    def _found(self, text, requested=False):
        return list(complete.GateCompleter().get_completions(_Document(text), _Event(requested)))

    def test_replaces_only_the_word(self, repo):
        found = self._found("git pu")
        assert found[0].text == "push "  # 고르면 공백까지 넣는다
        assert found[0].start_position == -2

    def test_too_many_candidates_hidden_unless_tab(self, repo, monkeypatch):
        monkeypatch.setattr(complete, "MAX_CANDIDATES", 1)
        assert self._found("git ") == []
        assert self._found("git ", requested=True)

    def test_slow_list_hidden_unless_tab(self, repo, monkeypatch):
        monkeypatch.setattr(complete, "BUDGET_SECONDS", 0)
        assert self._found("git ") == []
        assert self._found("git ", requested=True)


@pytest.mark.skipif(not shutil.which("git"), reason="git 필요")
class TestRealTerminal:
    """가상 터미널에 실제 cligate 를 띄워 키를 넣고 화면을 읽는다."""

    def _spawn(self, cwd):
        import pty
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(cwd)
            os.environ["PROMPT_TOOLKIT_NO_CPR"] = "1"
            os.environ["TERM"] = "xterm"
            os.execv(sys.executable, [sys.executable, "-c", "from cligate.cli import main; main()"])
        return pid, fd

    def _read(self, fd, seconds):
        out = b""
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    break
        return out.decode("utf-8", "replace")

    def _quit(self, pid, fd):
        os.write(fd, b"\x15\x04")  # 줄 지우기 후 Ctrl-D
        self._read(fd, 0.3)
        os.waitpid(pid, 0)
        os.close(fd)

    def test_opens_after_idle_not_while_typing(self, repo):
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"git pu")
        early = self._read(fd, 0.2)
        late = self._read(fd, 1.0)
        self._quit(pid, fd)
        assert "push" not in early
        assert "push" in late and "pull" in late

    def test_typing_restarts_the_timer(self, repo):
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        seen = ""
        for ch in b"git pu":
            os.write(fd, bytes([ch]))
            seen += self._read(fd, 0.2)
        self._quit(pid, fd)
        assert "pull" not in seen

    def test_tab_opens_immediately(self, repo):
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"git pu\t")
        shown = self._read(fd, 0.3)
        self._quit(pid, fd)
        assert "pull" in shown

    def test_warning_mark_is_on_screen(self, pushed):
        pid, fd = self._spawn(pushed)
        self._read(fd, 1.0)
        os.write(fd, b"git push --force")
        shown = self._read(fd, 1.2)
        self._quit(pid, fd)
        assert "⚠" in shown

    def test_enter_still_judges_and_runs(self, repo):
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"rm a\r")
        self._read(fd, 1.0)
        self._quit(pid, fd)
        assert not (repo / "a").exists()

    def test_single_candidate_continues_to_next_step(self, repo):
        git("branch", "side")
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"git switc")
        self._read(fd, 1.0)
        os.write(fd, b"\t")
        shown = self._read(fd, 1.2)
        os.write(fd, b"side\r")  # 공백이 들어갔으면 git switch side 가 된다
        self._read(fd, 1.0)
        self._quit(pid, fd)
        assert "side" in shown  # 다음 후보인 브랜치 목록
        assert git("branch", "--show-current", cwd=repo) == "side"

    def test_hint_on_screen_but_not_on_line(self, repo):
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"git bra")
        shown = self._read(fd, 1.2)
        os.write(fd, b"\t\r")  # 예시가 줄에 들어갔다면 <이름> 을 파일로 읽으려다 실패한다
        result = self._read(fd, 1.0)
        self._quit(pid, fd)
        assert "<이름>" in shown
        assert "* main" in re.sub(r"\x1b\[[0-9;]*m", "", result)  # git branch 가 그대로 실행되어 브랜치 목록이 나온다

    def test_space_accepts_and_continues_when_several(self, pushed):
        pid, fd = self._spawn(pushed)
        self._read(fd, 1.0)
        os.write(fd, b"git pu")
        self._read(fd, 1.0)
        os.write(fd, b"\t")
        self._read(fd, 0.2)
        os.write(fd, b" ")
        shown = self._read(fd, 1.2)
        self._quit(pid, fd)
        assert "origin" in shown

    def test_selecting_while_several_does_not_jump(self, pushed):
        pid, fd = self._spawn(pushed)
        self._read(fd, 1.0)
        os.write(fd, b"git pu")
        self._read(fd, 1.0)
        os.write(fd, b"\t")
        shown = self._read(fd, 1.2)
        self._quit(pid, fd)
        assert "origin" not in shown

    def test_no_repeat_after_finished_merge(self, repo):
        git("branch", "side")
        git("branch", "other")
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"git merge ")
        first = self._read(fd, 1.2)
        os.write(fd, b"side ")
        later = self._read(fd, 1.2)
        self._quit(pid, fd)
        assert "other" in first
        assert "other" not in later

    def test_selecting_candidate_inserts_it(self, repo):
        pid, fd = self._spawn(repo)
        self._read(fd, 1.0)
        os.write(fd, b"touch made-by-echo; ech")
        self._read(fd, 1.0)
        os.write(fd, b"\t\r")
        self._read(fd, 1.0)
        self._quit(pid, fd)
        assert (repo / "made-by-echo").exists()
