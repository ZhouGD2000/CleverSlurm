from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StaticCommand:
    kind: str
    executable: str
    argv: list[str]
    entry_file: str | None
    entry_file_abs: str | None


@dataclass(frozen=True)
class StaticInsertResult:
    commands: int
    files: int


def resolve_entry(path_text: str, script_dir: Path, suffix: str) -> tuple[str, str | None]:
    entry = path_text if path_text.endswith(suffix) else path_text + suffix
    path = Path(entry).expanduser()
    if not path.is_absolute():
        path = script_dir / path
    return Path(entry).name, str(path.resolve()) if path.exists() else None
