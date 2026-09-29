import os


def cd(args):
    if not args:
        target = "~"
    elif args[0] == "-":
        target = os.environ.get("OLDPWD")
        if not target:
            print("cd: OLDPWD not set")
            return
        print(target)
    else:
        target = args[0]
    try:
        old = os.getcwd()
        os.chdir(os.path.expanduser(os.path.expandvars(target)))
    except OSError as e:
        print(f"cd: {e}")
        return
    # sh 에서 $PWD, cd - 가 맞게 동작하도록 환경변수도 갱신
    os.environ["OLDPWD"] = old
    os.environ["PWD"] = os.getcwd()


def exit_(args):
    code = 0
    if args:
        try:
            code = int(args[0]) & 0xFF
        except ValueError:
            print(f"exit: {args[0]}: numeric argument required")
            code = 2
    raise SystemExit(code)


def export(args):
    if not args:
        for name, value in sorted(os.environ.items()):
            print(f"export {name}={value}")
        return
    for arg in args:
        name, sep, value = arg.partition("=")
        if not (name.isascii() and name.isidentifier()):
            print(f"export: `{arg}': not a valid identifier")
            continue
        # CliGate 에는 export 되지 않은 변수가 없으므로 값 없는 export NAME 은 할 일이 없다
        if sep:
            # shlex 는 $VAR, ~ 를 펼치지 않으므로 export PATH=$PATH:~/bin 을 위해 직접 펼친다
            os.environ[name] = os.path.expanduser(os.path.expandvars(value))


def unset(args):
    for name in args:
        if not name.startswith("-"):
            os.environ.pop(name, None)


BUILTINS = {
    "cd": cd,
    "exit": exit_,
    "export": export,
    "unset": unset,
}
