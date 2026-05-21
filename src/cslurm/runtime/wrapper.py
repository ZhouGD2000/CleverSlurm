import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry_file(kind: str, argv: list[str]) -> str | None:
    suffixes = {
        "julia": ".jl",
        "python": ".py",
        "matlab": ".m",
        "bash": ".sh",
    }
    suffix = suffixes.get(kind)
    if suffix is None:
        return None
    for arg in argv:
        if arg.endswith(suffix):
            return arg
    return None


def write_command_log(kind: str, real_executable: str, argv: list[str]) -> None:
    log_dir = Path(os.environ["CSLURM_LOG_DIR"])
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "time": _now(),
        "job_id": os.environ.get("CSLURM_JOB_ID", "unknown"),
        "hostname": socket.gethostname(),
        "cwd": os.getcwd(),
        "kind": kind,
        "executable": real_executable,
        "argv": argv,
        "entry_file": _entry_file(kind, argv),
    }
    with (log_dir / "commands.log").open("a") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m cslurm.runtime.wrapper KIND [ARGS...]")
    kind = sys.argv[1]
    argv = sys.argv[2:]
    real_env_name = f"CSLURM_REAL_{kind.upper()}"
    real = os.environ.get(real_env_name)
    if not real:
        raise SystemExit(f"missing {real_env_name}")
    write_command_log(kind, real, argv)
    os.execv(real, [real, *argv])


if __name__ == "__main__":
    main()
