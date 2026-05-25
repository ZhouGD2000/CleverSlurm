import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from cslurm.shims import MANAGED_MARKER


REQUIRED_SLURM_COMMANDS = ("sbatch", "sacct", "squeue", "sinfo", "srun", "scancel")
FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class RealSlurmProbe:
    available: bool
    reason: str | None
    commands: dict[str, str]


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _is_managed_cleverslurm_shim(path: Path) -> bool:
    try:
        prefix = path.open("rb").read(4096).decode(errors="replace")
    except OSError:
        return False
    return MANAGED_MARKER in prefix


def resolve_real_command(command: str, environ: Mapping[str, str] = os.environ) -> str | None:
    override = environ.get(f"CSLURM_{command.upper()}")
    if override:
        return override

    for part in environ.get("PATH", "").split(os.pathsep):
        if not part:
            continue
        candidate = Path(part) / command
        if not candidate.exists() or not _is_executable(candidate):
            continue
        if _is_managed_cleverslurm_shim(candidate):
            continue
        return str(candidate)
    return None


def probe_real_slurm(
    *,
    command_resolver: Callable[[str, Mapping[str, str]], str | None] = resolve_real_command,
    run: Callable = subprocess.run,
    environ: Mapping[str, str] = os.environ,
    required_commands: tuple[str, ...] = REQUIRED_SLURM_COMMANDS,
) -> RealSlurmProbe:
    flag = environ.get("CSLURM_RUN_REAL_SLURM")
    if flag and flag.strip().lower() in FALSE_VALUES:
        return RealSlurmProbe(False, f"disabled by CSLURM_RUN_REAL_SLURM={flag}", {})

    commands = {}
    for command in required_commands:
        path = command_resolver(command, environ)
        if not path:
            return RealSlurmProbe(False, f"missing Slurm command: {command}", commands)
        commands[command] = path

    for command, path in commands.items():
        try:
            result = run(
                [path, "--version"],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RealSlurmProbe(False, f"{command} --version failed: {exc}", commands)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
            return RealSlurmProbe(False, f"{command} --version failed: {detail}", commands)

    return RealSlurmProbe(True, None, commands)


def first_available_partition(
    *,
    run: Callable = subprocess.run,
    sinfo: str | None = None,
    environ: Mapping[str, str] = os.environ,
) -> str | None:
    configured = environ.get("CSLURM_REAL_SLURM_PARTITION")
    if configured:
        return configured

    command = sinfo or resolve_real_command("sinfo", environ)
    if not command:
        return None
    try:
        result = run(
            [command, "-h", "-o", "%P %t"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    for raw_line in result.stdout.splitlines():
        parts = raw_line.split()
        if not parts:
            continue
        state = parts[1].lower() if len(parts) > 1 else ""
        if state and state not in {"up", "idle", "mix", "alloc"}:
            continue
        return parts[0].rstrip("*")
    return None
