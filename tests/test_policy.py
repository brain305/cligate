"""팀 규칙(정책) 1단계. 규칙은 올리기만 하고, 틀린 파일은 그 파일만 무시한다."""
import datetime
import gzip
import json
import socket
import subprocess
import sys

import pytest
from conftest import git, judge, level, write

from cligate import audit, gate, policy, policycmd
from cligate.risk import L0, L1, L2, L3


def user_rules(text):
    path = policy.user_file()
    write(__import__("pathlib").Path(path), text)
    return path


def rule(**fields):
    lines = ["[[rule]]"]
    for key, value in {"id": "r1", "command": "touch", "level": 3, "message": "막음", "mode": "enforce",
                       **fields}.items():
        if value is not None:
            lines.append(f"{key} = {_toml(value)}")
    return "\n".join(lines) + "\n"


def _toml(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{k} = {_toml(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def matched(line, **fields):
    """규칙 하나만 둔 상태에서 그 줄의 판정에 규칙이 걸렸는지."""
    user_rules(rule(**fields))
    return any(f.rule is not None for f in judge(line).findings)


def cmd(*argv):
    return policycmd.main(list(argv))


class TestValidate:
    def load(self, text, tmp_path, strict=False):
        path = tmp_path / "p.toml"
        write(path, text)
        return policy.load(str(path), policy.USER, strict=strict)

    def test_minimal_rule_defaults_to_observe(self, tmp_path):
        loaded = self.load('[[rule]]\nid = "a"\ncommand = "rm"\nlevel = 2\nmessage = "m"\n', tmp_path)
        assert loaded.errors == []
        assert loaded.rules[0].mode == policy.OBSERVE
        assert loaded.rules[0].command == ("rm",)

    @pytest.mark.parametrize("text, problem", [
        (rule(foo=1), "모르는 필드"),
        ('[[rule]]\nid = "a"\ncommand = "rm"\nlevel = 2\n', "message 가 없습니다"),
        (rule(level=0), "올리기만"),
        (rule(level=4), "1~3"),
        (rule(mode="block"), "mode"),
        (rule(word="two words"), "한 단어"),
        (rule(when={"color": "red"}), "모르는 조건"),
        (rule(when={"count": "many"}), "정수"),
        (rule(args=["[abc"]), "짝이 맞지"),
        (rule(id="공백 있는 id"), "id 는"),
        (rule(tests=[{"command": "rm", "expect": 9}]), "tests"),
        ("[unknown]\nx = 1\n", "모르는 항목"),
        ("[[rule]\n", "TOML 형식"),
    ])
    def test_problems_are_reported(self, tmp_path, text, problem):
        loaded = self.load(text, tmp_path)
        assert any(problem in error for error in loaded.errors), loaded.errors

    def test_duplicate_id(self, tmp_path):
        assert any("겹칩니다" in e for e in self.load(rule() + rule(), tmp_path).errors)

    def test_expired_rule_is_an_error_only_for_check(self, tmp_path):
        text = rule(expires="2020-01-01") + rule(id="r2")
        assert self.load(text, tmp_path).errors == []
        assert any("지났습니다" in e for e in self.load(text, tmp_path, strict=True).errors)


class TestMatch:
    def test_command_and_subcommand(self, repo):
        assert matched("terraform -chdir=infra destroy", command="terraform destroy")
        assert not matched("terraform plan", command="terraform destroy")
        assert matched("sudo touch x", command="touch")

    def test_inside_other_commands(self, repo):
        assert matched("sh -c 'touch x'", command="touch")
        assert matched("ls && touch x", command="touch")

    def test_args_sequence(self, repo):
        assert matched("kubectl delete pod web -n prod", command="kubectl delete", args=["-n prod"])
        assert matched("kubectl delete pod web --namespace=prod", command="kubectl delete", args=["--namespace=prod"])
        assert not matched("kubectl delete pod web -n dev", command="kubectl delete", args=["-n prod"])

    def test_path_glob_includes_the_folder_itself(self, repo):
        assert matched("rm -rf /data", command="rm", path=["/data/**"])
        assert matched("rm /data/x/y", command="rm", path=["/data/**"])
        assert not matched("rm -rf /database", command="rm", path=["/data/**"])

    def test_path_follows_glob_and_relative(self, repo):
        assert matched("rm logs/*.log", command="rm", path=[str(repo / "logs" / "*")])

    def test_branch_of_push_destination(self, repo):
        git("checkout", "-q", "-b", "feature", cwd=repo)
        assert matched("git push origin main", command="git push", when={"branch": ["main"]})
        assert matched("git push origin feature:main", command="git push", when={"branch": ["main"]})
        assert not matched("git push origin feature", command="git push", when={"branch": ["main"]})

    def test_branch_of_other_commands_is_current(self, repo):
        assert matched("git reset --hard", command="git reset", when={"branch": ["ma*"]})
        git("checkout", "-q", "-b", "feature", cwd=repo)
        assert not matched("git reset --hard", command="git reset", when={"branch": ["main"]})

    @pytest.mark.parametrize("line, forced", [
        ("git push --force", True), ("git push -f origin main", True), ("git push origin +main", True),
        ("git push --force-with-lease", True), ("git push origin main", False),
    ])
    def test_forced(self, repo, line, forced):
        assert matched(line, command="git push", when={"forced": True}) is forced

    def test_host_user_and_cwd(self, repo, monkeypatch):
        monkeypatch.setattr(socket, "gethostname", lambda: "prod-db1.corp")
        assert matched("touch x", when={"host": ["prod-*"]})
        assert not matched("touch x", when={"host": ["dev-*"]})
        assert matched("touch x", when={"cwd": [str(repo.parent) + "/**"]})
        assert not matched("touch x", when={"user": ["nobody-here"]})

    def test_size_and_untracked(self, repo):
        for i in range(3):
            write(repo / "new" / f"{i}.txt", "x" * 100)
        assert matched("rm -rf new", command="rm", when={"count": 3})
        assert not matched("rm -rf new", command="rm", when={"count": 4})
        assert matched("rm -rf new", command="rm", when={"bytes": 300})
        assert matched("rm -rf new", command="rm", when={"untracked": True})
        assert not matched("rm a", command="rm", when={"untracked": True})


class TestApply:
    def test_enforce_raises_level_and_sets_word(self, repo):
        user_rules(rule(word="infra"))
        verdict = judge("touch x")
        assert verdict.level == L3
        assert verdict.word == "infra"
        assert "개인 규칙 r1: 막음" in verdict.findings[0].message

    def test_rule_never_lowers_builtin(self, repo):
        user_rules(rule(command="rm", level=1))
        verdict = judge("rm -rf /")
        assert verdict.level == L3
        assert verdict.word == "delete"

    def test_observe_does_not_change_level(self, repo):
        user_rules(rule(mode="observe"))
        verdict = judge("touch x")
        assert verdict.level == L0
        assert [r.id for r in verdict.observed] == ["r1"]

    def test_expired_rule_is_off_but_others_work(self, repo):
        user_rules(rule(expires="2020-01-01") + rule(id="r2", command="mkdir", level=2))
        assert level("touch x") == L0
        assert level("mkdir y") == L2

    def test_broken_file_is_ignored(self, repo):
        user_rules(rule(level=9))
        assert level("touch x") == L0

    def test_owner_and_suggest_shown(self, repo):
        user_rules(rule(owner="운영팀", suggest="touch -c x"))
        message = judge("touch x").findings[0].message
        assert "담당  운영팀" in message and "권장  touch -c x" in message

    def test_completion_preview_uses_rules(self, repo):
        user_rules(rule(level=2))
        assert gate.preview("touch x").level == L2

    def test_changed_file_is_read_again(self, repo):
        user_rules(rule(level=1))
        assert level("touch x") == L1
        user_rules(rule(level=2) + "# 크기를 바꿔 다시 읽게 한다\n")
        assert level("touch x") == L2


class TestProject:
    def setup_project(self, repo, text):
        write(repo / ".cligate" / "policy.toml", text)
        return str(repo / ".cligate" / "policy.toml")

    def test_untrusted_until_approved(self, repo):
        path = self.setup_project(repo, rule())
        assert level("touch x") == L0
        assert cmd("trust", "--yes") == 0
        assert level("touch x") == L3
        write(__import__("pathlib").Path(path), rule(level=2))
        assert level("touch x") == L0  # 바뀌면 다시 승인해야 한다

    def test_found_from_subfolder(self, repo, monkeypatch):
        self.setup_project(repo, rule())
        cmd("trust", "--yes")
        monkeypatch.chdir(repo / "d")
        assert level("touch x") == L3

    def test_project_comes_before_personal_for_the_word(self, repo):
        self.setup_project(repo, rule(word="project"))
        cmd("trust", "--yes")
        user_rules(rule(word="personal"))
        assert judge("touch x").word == "project"

    def test_suggestion_is_marked(self, repo):
        self.setup_project(repo, rule(suggest="echo hi"))
        cmd("trust", "--yes")
        assert "저장소 규칙의 제안" in judge("touch x").findings[0].message

    def test_trust_refuses_broken_file(self, repo, capsys):
        self.setup_project(repo, rule(level=0))
        assert cmd("trust", "--yes") == 1
        assert "오류" in capsys.readouterr().out


class TestAddRemove:
    def test_add_creates_enforced_rule(self, repo, capsys):
        assert cmd("add", "--command", "touch", "--level", "3", "--message", "막음") == 0
        out = capsys.readouterr().out
        assert "touch-1" in out and "(enforce)" in out
        assert level("touch x") == L3

    def test_add_with_conditions(self, repo):
        cmd("add", "--command", "git push", "--branch", "main", "--level", "2", "--message", "m", "--observe")
        loaded = policy.load(policy.user_file(), policy.USER)
        assert loaded.rules[0].when == {"branch": ("main",)}
        assert loaded.rules[0].mode == policy.OBSERVE

    def test_add_numbers_ids(self, repo):
        for _ in range(2):
            cmd("add", "--command", "touch", "--level", "1", "--message", "m")
        assert [r.id for r in policy.load(policy.user_file(), policy.USER).rules] == ["touch-1", "touch-2"]

    def test_add_refuses_invalid(self, repo, capsys):
        assert cmd("add", "--command", "touch", "--level", "1", "--message", "m", "--word", "two words") == 1
        assert "추가하지 않았습니다" in capsys.readouterr().out
        assert not __import__("os").path.exists(policy.user_file())

    def test_add_refuses_broken_file(self, repo):
        user_rules(rule(level=0))
        assert cmd("add", "--command", "touch", "--level", "1", "--message", "m") == 1

    def test_remove_keeps_other_rules_and_comments(self, repo):
        path = user_rules("# 내 규칙\n" + rule(id="a") + "\n# 둘째\n" + rule(id="b", command="mkdir"))
        assert cmd("remove", "a", "--yes") == 0
        text = open(path, encoding="utf-8").read()
        assert "# 내 규칙" in text and "# 둘째" in text
        assert [r.id for r in policy.load(path, policy.USER).rules] == ["b"]

    def test_remove_asks_first(self, repo, monkeypatch):
        user_rules(rule(id="a"))
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert cmd("remove", "a") == 1
        assert level("touch x") == L3

    def test_remove_points_to_project_rules(self, repo, capsys):
        write(repo / ".cligate" / "policy.toml", rule(id="proj"))
        assert cmd("remove", "proj", "--yes") == 1
        assert "프로젝트 규칙입니다" in capsys.readouterr().out

    def test_remove_suggests_close_ids(self, repo, capsys):
        user_rules(rule(id="touch-prod"))
        assert cmd("remove", "touch-prd", "--yes") == 1
        assert "touch-prod" in capsys.readouterr().out


class TestCheckTestExplain:
    def test_check(self, tmp_path, capsys):
        good, bad = tmp_path / "good.toml", tmp_path / "bad.toml"
        write(good, rule())
        write(bad, rule(level=0))
        assert cmd("check", str(good)) == 0
        assert cmd("check", str(good), str(bad)) == 1
        assert "오류 1" in capsys.readouterr().out

    def test_test_runs_examples(self, repo, tmp_path, capsys):
        path = tmp_path / "t.toml"
        write(path, rule(command="git push", when={"branch": ["main"]}, tests=[
            {"command": "git push origin main", "expect": 3}, {"command": "git push origin dev", "expect": 0}]))
        assert cmd("test", str(path)) == 0
        assert "2/2 통과" in capsys.readouterr().out
        write(path, rule(tests=[{"command": "touch x", "expect": 0}]))
        assert cmd("test", str(path)) == 1

    def test_explain_lists_rules_and_final(self, repo, capsys):
        user_rules(rule(id="enf", level=2) + rule(id="obs", level=3, mode="observe"))
        assert cmd("explain", "touch x") == 0
        out = capsys.readouterr().out
        assert "기본 판정" in out and "enf" in out and "관찰 모드라 지금은 기록만" in out
        assert out.strip().splitlines()[-1].split()[-1] == "L2"


class TestHits:
    def write_events(self, path, events, compress=False):
        lines = "".join(json.dumps(e) + "\n" for e in events)
        if compress:
            with gzip.open(path, "wt", encoding="utf-8") as f:
                f.write(lines)
        else:
            path.write_text(lines, encoding="utf-8")

    def event(self, id, host="web1", rule="r1", days_ago=0):
        when = datetime.datetime.now().astimezone() - datetime.timedelta(days=days_ago)
        return {"id": id, "time": when.isoformat(timespec="seconds"), "host": host, "user": "kim",
                "policy": "team", "version": 3, "rule": rule, "mode": "observe", "result": "passed"}

    def test_merges_files_and_skips_duplicates_and_bad_lines(self, tmp_path):
        a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl.gz"
        self.write_events(a, [self.event("1"), self.event("2", host="web2")])
        self.write_events(b, [self.event("2", host="web2"), self.event("3", host="web3")], compress=True)
        with open(a, "a", encoding="utf-8") as f:
            f.write("not json\n{\"id\": \"x\"}\n")
        summary = audit.summarize([str(a), str(b)], by="host")
        row = summary.rows[("team", 3, "r1")]
        assert (row.count, len(row.hosts), summary.duplicates, summary.unreadable) == (3, 3, 1, 2)
        assert row.by == {"web1": 1, "web2": 1, "web3": 1}

    def test_since_and_rule_filters(self, tmp_path):
        path = tmp_path / "a.jsonl"
        self.write_events(path, [self.event("1", days_ago=10), self.event("2"), self.event("3", rule="r2")])
        summary = audit.summarize([str(path)], since=audit.parse_since("7d"), rule="r1")
        assert summary.rows[("team", 3, "r1")].count == 1
        assert ("team", 3, "r2") not in summary.rows

    def test_parse_since(self):
        with pytest.raises(ValueError):
            audit.parse_since("7x")

    def test_record_and_count_with_zero_rows(self, repo, capsys):
        user_rules(rule(id="seen", mode="observe") + rule(id="never", command="mkdir", mode="observe"))
        audit.record_observed(judge("touch x").observed, str(repo), audit.PASSED)
        assert cmd("hits") == 0
        out = capsys.readouterr().out
        assert "personal/seen" in out and "personal/never" in out
        recorded = json.loads(open(audit.observe_path(), encoding="utf-8").readline())
        assert recorded["command"] == "touch" and recorded["result"] == "passed"

    def test_command_arguments_are_not_recorded(self, repo):
        user_rules(rule(mode="observe"))
        audit.record_observed(judge("touch secret-token-123").observed, str(repo), audit.PASSED)
        assert "secret-token-123" not in open(audit.observe_path(), encoding="utf-8").read()


class TestEndToEnd:
    def run(self, lines, cwd):
        return subprocess.run([sys.executable, "-c", "from cligate.cli import main; main()"],
                              input="\n".join([*lines, ""]), cwd=cwd, capture_output=True, text=True, timeout=30)

    def test_enforced_rule_blocks_until_word(self, repo):
        user_rules(rule(word="ok"))
        self.run(["touch z", "nope", "exit"], repo)
        assert not (repo / "z").exists()
        self.run(["touch z", "ok", "exit"], repo)
        assert (repo / "z").exists()

    def test_observe_records_passed_and_cancelled(self, repo):
        user_rules(rule(command="rm", mode="observe"))
        write(repo / "new.txt")
        self.run(["rm new.txt", "n", "exit"], repo)  # 기본 판정이 L2 라 묻고, 취소한다
        self.run(["rm a", "exit"], repo)  # 커밋된 파일은 L1 이라 바로 실행된다
        results = [json.loads(line)["result"] for line in open(audit.observe_path(), encoding="utf-8")]
        assert results == ["cancelled", "passed"]
        assert (repo / "new.txt").exists() and not (repo / "a").exists()

    def test_broken_policy_is_announced(self, repo):
        user_rules(rule(level=0))
        result = self.run(["exit"], repo)
        assert "오류가 있어 쓰지 않습니다" in result.stdout + result.stderr

    def test_policy_subcommand(self, repo):
        result = subprocess.run([sys.executable, "-c", "from cligate.cli import main; main()", "policy", "show"],
                                cwd=repo, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        assert result.returncode == 0
        assert "적용 중인 규칙이 없습니다" in result.stdout
