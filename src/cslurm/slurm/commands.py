import os
import shutil
import subprocess
from pathlib import Path

from cslurm.config import command_path
from cslurm.shims import MANAGED_MARKER


def run_slurm_command(name: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    command = command_path(name)
    env = None
    if command == name:
        path = shutil.which(name)
        if path and _is_cleverslurm_shim(Path(path)):
            env = os.environ.copy()
            env["PATH"] = _path_without(Path(path).resolve().parent)
    return subprocess.run(
        [command, *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def _is_cleverslurm_shim(path: Path) -> bool:
    try:
        return MANAGED_MARKER in path.read_text(errors="replace")
    except OSError:
        return False


def _path_without(path_to_remove: Path) -> str:
    remove = path_to_remove.expanduser().resolve()
    kept = []
    for raw_part in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_part:
            continue
        try:
            part = Path(raw_part).expanduser().resolve()
        except OSError:
            kept.append(raw_part)
            continue
        if part != remove:
            kept.append(raw_part)
    return os.pathsep.join(kept)
