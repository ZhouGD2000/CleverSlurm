import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path


MANAGED_MARKER = "# CleverSlurm managed Slurm command shim"

SHIM_COMMANDS = {
    "sbatch": ("csbatch", "CSLURM_SBATCH"),
    "srun": ("csrun", "CSLURM_SRUN"),
    "scancel": ("cscancel", "CSLURM_SCANCEL"),
}


@dataclass(frozen=True)
class ShimInstallResult:
    slurm_command: str
    target: Path
    clever_command: str
    clever_path: Path
    real_path: Path


@dataclass(frozen=True)
class ShimStatus:
    slurm_command: str
    target: Path
    installed: bool
    active_path: str | None
    real_path: str | None


def default_bin_dir() -> Path:
    for command in ("csbatch", "csrun", "cscancel"):
        path = shutil.which(command)
        if path:
            return Path(path).resolve().parent
    raise RuntimeError("Could not find csbatch, csrun, or cscancel on PATH. Install CleverSlurm first.")


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


def _which_outside_bin(command: str, bin_dir: Path) -> str | None:
    return shutil.which(command, path=_path_without(bin_dir))


def _is_managed(path: Path) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    try:
        return MANAGED_MARKER in path.read_text(errors="replace")
    except OSError:
        return False


def _wrapper_text(*, slurm_command: str, clever_path: Path, real_path: Path, env_name: str) -> str:
    return "\n".join(
        [
            "#!/bin/sh",
            MANAGED_MARKER,
            f"# {slurm_command} -> {clever_path.name}",
            f"export {env_name}={shlex.quote(str(real_path))}",
            f"exec {shlex.quote(str(clever_path))} \"$@\"",
            "",
        ]
    )


def install(*, bin_dir: Path | None = None, force: bool = False) -> list[ShimInstallResult]:
    target_bin = (bin_dir or default_bin_dir()).expanduser().resolve()
    target_bin.mkdir(parents=True, exist_ok=True)
    results = []
    for slurm_command, (clever_command, env_name) in SHIM_COMMANDS.items():
        clever_path_raw = shutil.which(clever_command)
        if not clever_path_raw:
            raise RuntimeError(f"Could not find {clever_command} on PATH. Install CleverSlurm console scripts first.")
        clever_path = Path(clever_path_raw).resolve()
        real_path_raw = _which_outside_bin(slurm_command, target_bin)
        if not real_path_raw:
            raise RuntimeError(f"Could not find the real {slurm_command} outside {target_bin}.")
        real_path = Path(real_path_raw).resolve()
        target = target_bin / slurm_command
        if (target.exists() or target.is_symlink()) and not _is_managed(target) and not force:
            raise RuntimeError(f"Refusing to overwrite unmanaged command: {target}")
        target.write_text(
            _wrapper_text(
                slurm_command=slurm_command,
                clever_path=clever_path,
                real_path=real_path,
                env_name=env_name,
            )
        )
        target.chmod(0o755)
        results.append(
            ShimInstallResult(
                slurm_command=slurm_command,
                target=target,
                clever_command=clever_command,
                clever_path=clever_path,
                real_path=real_path,
            )
        )
    return results


def remove(*, bin_dir: Path | None = None) -> list[Path]:
    target_bin = (bin_dir or default_bin_dir()).expanduser().resolve()
    removed = []
    for slurm_command in SHIM_COMMANDS:
        target = target_bin / slurm_command
        if _is_managed(target):
            target.unlink()
            removed.append(target)
    return removed


def status(*, bin_dir: Path | None = None) -> list[ShimStatus]:
    target_bin = (bin_dir or default_bin_dir()).expanduser().resolve()
    rows = []
    for slurm_command in SHIM_COMMANDS:
        target = target_bin / slurm_command
        rows.append(
            ShimStatus(
                slurm_command=slurm_command,
                target=target,
                installed=_is_managed(target),
                active_path=shutil.which(slurm_command),
                real_path=_which_outside_bin(slurm_command, target_bin),
            )
        )
    return rows
