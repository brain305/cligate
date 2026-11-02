import pytest

from cligate import parse


@pytest.mark.parametrize("line, first_real", [
    ("rm a", "rm"),
    ("sudo rm a", "rm"),
    ("sudo -u root rm a", "rm"),
    ("env -u X rm a", "rm"),
    ("FOO=1 rm a", "rm"),
    ("time rm a", "rm"),
    ("nohup rm a", "rm"),
    ("xargs -n1 rm", "rm"),
    ("xargs -I {} rm {}", "rm"),
    ("command -v rm", "command"),
])
def test_unwrap(line, first_real):
    real, _ = parse.unwrap(parse.parse(line)[0])
    assert real[0] == first_real


@pytest.mark.parametrize("line, count", [
    ("ls; pwd", 2),
    ("ls && pwd || id", 3),
    ("ls | wc", 2),
    ("(ls); pwd", 2),
    ('echo "a; b"', 1),
    ("ls 2>&1", 1),
    ("echo x > out", 1),
    ("ls # rm x; rm y", 1),
])
def test_command_count(line, count):
    assert len(parse.parse(line)) == count


def test_substitution_is_checked_too():
    names = [argv[0] for argv in parse.parse('echo "$(rm a)"')]
    assert "rm" in names


def test_single_quotes_are_not_substitution():
    assert all(argv[0] != "rm" for argv in parse.parse("echo '$(rm a)'"))


@pytest.mark.parametrize("line", ['echo "hello', "ls ||", 'rm "a'])
def test_syntax_error(line):
    assert parse.syntax_error(line)


def test_valid_syntax():
    assert parse.syntax_error("ls -la && pwd") is None


@pytest.mark.parametrize("words, kept", [
    (["a", "2", ">&", "1"], ["a"]),
    (["a", ">", "out"], ["a"]),
    (["a", "2", ">", "/dev/null", "b"], ["a", "b"]),
    (["a", "b"], ["a", "b"]),
])
def test_strip_redirects(words, kept):
    assert parse.strip_redirects(words) == kept
