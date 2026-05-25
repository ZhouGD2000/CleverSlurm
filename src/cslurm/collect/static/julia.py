import re
from pathlib import Path


INCLUDE_STRING = re.compile(r"""include\(\s*["']([^"']+)["']\s*\)""")
INCLUDE_JOINPATH = re.compile(r"""include\(\s*joinpath\(([^)]*)\)\s*\)""")
QUOTED_PART = re.compile(r"""["']([^"']+)["']""")


def find_julia_dependencies(entry_file: Path) -> set[Path]:
    entry_file = entry_file.resolve()
    root = entry_file.parent
    text = entry_file.read_text()
    deps: set[Path] = set()

    for match in INCLUDE_STRING.finditer(text):
        path = (root / match.group(1)).resolve()
        if path.exists():
            deps.add(path)

    for match in INCLUDE_JOINPATH.finditer(text):
        parts = QUOTED_PART.findall(match.group(1))
        if parts:
            path = root.joinpath(*parts).resolve()
            if path.exists():
                deps.add(path)

    return deps
