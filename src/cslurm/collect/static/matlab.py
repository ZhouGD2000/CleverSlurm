import re
from pathlib import Path

from cslurm.collect.static.common import resolve_entry


MATLAB_NAMES = {"matlab", "matlab.exe"}


def is_matlab_executable(executable: str) -> bool:
    return Path(executable).name.lower() in MATLAB_NAMES


def entry_from_argv(argv: list[str], script_dir: Path) -> tuple[str | None, str | None]:
    for index, arg in enumerate(argv):
        if arg in {"-r", "-batch"} and index + 1 < len(argv):
            expression = argv[index + 1].strip()
            candidate = re.split(r"[\s(;]", expression, maxsplit=1)[0].strip("'\"")
            if candidate and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./-]*(?:\.m)?", candidate):
                return resolve_entry(candidate, script_dir, ".m")
    return None, None
