import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from cslurm.config import root_dir


BEGIN_MARKER = "# BEGIN CleverSlurm ctrack"
END_MARKER = "# END CleverSlurm ctrack"


@dataclass(frozen=True)
class AutoTrackStatus:
    enabled: bool
    line: str | None = None


def _strip_managed_block(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    in_block = False
    for line in lines:
        if line.strip() == BEGIN_MARKER:
            in_block = True
            continue
        if line.strip() == END_MARKER:
            in_block = False
            continue
        if not in_block:
            kept.append(line)
    return "\n".join(kept).rstrip()


def _managed_line(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != BEGIN_MARKER:
            continue
        for candidate in lines[index + 1 :]:
            if candidate.strip() == END_MARKER:
                return None
            if candidate.strip() and not candidate.lstrip().startswith("#"):
                return candidate
    return None


def _default_repo_dir() -> Path:
    cwd = Path.cwd()
    if (cwd / "src" / "cslurm").is_dir():
        return cwd
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "src" / "cslurm").is_dir():
        return package_root
    return cwd


def build_cron_line(
    *,
    repo_dir: Path | None = None,
    python_executable: str | None = None,
    schedule: str = "* * * * *",
) -> str:
    repo = (repo_dir or _default_repo_dir()).expanduser().resolve()
    root = root_dir().expanduser()
    python = python_executable or sys.executable
    path = os.environ.get("PATH") or "/usr/bin:/bin:/usr/local/bin"
    lock = root / "ctrack.lock"
    log = root / "ctrack.log"
    env_parts = [
        f"HOME={shlex.quote(str(Path.home()))}",
        f"PATH={shlex.quote(path)}",
        f"CSLURM_ROOT={shlex.quote(str(root))}",
    ]
    if (repo / "src" / "cslurm").is_dir():
        env_parts.append(f"PYTHONPATH={shlex.quote(str(repo / 'src'))}")
    command = " ".join(
        [
            "cd",
            shlex.quote(str(repo)),
            "&&",
            "/usr/bin/flock",
            "-n",
            shlex.quote(str(lock)),
            "env",
            *env_parts,
            shlex.quote(python),
            "-m",
            "cslurm.cli.ctrack",
            ">>",
            shlex.quote(str(log)),
            "2>&1",
        ]
    )
    return f"{schedule} {command}"


def _read_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        return ""
    return result.stdout


def _write_crontab(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, check=True, text=True)


def status() -> AutoTrackStatus:
    line = _managed_line(_read_crontab())
    return AutoTrackStatus(enabled=line is not None, line=line)


def enable(*, repo_dir: Path | None = None, python_executable: str | None = None, schedule: str = "* * * * *") -> str:
    current = _strip_managed_block(_read_crontab())
    line = build_cron_line(repo_dir=repo_dir, python_executable=python_executable, schedule=schedule)
    block = "\n".join([BEGIN_MARKER, line, END_MARKER])
    updated = "\n".join(part for part in [current, block] if part).rstrip() + "\n"
    root_dir().mkdir(parents=True, exist_ok=True)
    _write_crontab(updated)
    return line


def disable() -> bool:
    current_raw = _read_crontab()
    was_enabled = _managed_line(current_raw) is not None
    if not was_enabled:
        return False
    current = _strip_managed_block(current_raw)
    _write_crontab((current + "\n") if current else "")
    return was_enabled


def restart(*, repo_dir: Path | None = None, python_executable: str | None = None, schedule: str = "* * * * *") -> str:
    return enable(repo_dir=repo_dir, python_executable=python_executable, schedule=schedule)
