import re
from pathlib import Path


PYTHON_NAMES = {"python", "python3", "python2", "python.exe"}


def is_python_executable(executable: str) -> bool:
    name = Path(executable).name.lower()
    return name in PYTHON_NAMES or bool(re.fullmatch(r"python\d+(\.\d+)?", name))


def entry_from_argv(argv: list[str], script_dir: Path) -> tuple[str | None, str | None]:
    for arg in argv:
        if arg.endswith(".py"):
            path = Path(arg).expanduser()
            if not path.is_absolute():
                path = script_dir / path
            return Path(arg).name, str(path.resolve()) if path.exists() else None
        if arg == "-m":
            return None, None
    return None, None
