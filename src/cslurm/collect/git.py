import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitMetadata:
    commit: str | None
    dirty: bool
    status: str
    diff: str


def _git(cwd: Path, args: list[str]) -> str | None:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout


def collect_git_metadata(cwd: Path) -> GitMetadata:
    commit = _git(cwd, ["rev-parse", "HEAD"])
    status = _git(cwd, ["status", "--porcelain"]) or ""
    diff = _git(cwd, ["diff"]) or ""
    return GitMetadata(
        commit=commit.strip() if commit else None,
        dirty=bool(status.strip() or diff.strip()),
        status=status,
        diff=diff,
    )
