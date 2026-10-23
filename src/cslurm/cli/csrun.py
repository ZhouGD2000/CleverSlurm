import os
import subprocess
from datetime import datetime, timezone

from cslurm.config import command_path
from cslurm.db import run_write_transaction
from cslurm.runtime.spool import publish
from cslurm.storage import require_local_database, UnsafeDatabaseError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_job_id() -> str:
    return "local-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def run_job(argv: list[str], *, capture_output: bool = True) -> subprocess.CompletedProcess[str]:
    if not argv:
        raise SystemExit("usage: csrun [srun-args...] command [args...]")

    allocation = os.environ.get("SLURM_JOB_ID")
    job_id = allocation or _local_job_id()
    if os.environ.get("SLURM_ARRAY_JOB_ID") and os.environ.get("SLURM_ARRAY_TASK_ID"):
        job_id = os.environ["SLURM_ARRAY_JOB_ID"] + "_" + os.environ["SLURM_ARRAY_TASK_ID"]
    spool = bool(allocation)
    try:
        require_local_database()
    except UnsafeDatabaseError:
        spool = True
    timestamp = _now()
    cwd = os.getcwd()
    cslurm_command = "csrun " + " ".join(argv)
    srun_command = "srun " + " ".join(argv)
    if capture_output:
        result = subprocess.run(
            [command_path("srun"), *argv],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    else:
        result = subprocess.run([command_path("srun"), *argv], check=False, text=True)
    state = "COMPLETED" if result.returncode == 0 else "FAILED"

    if spool:
        publish({"job_id": job_id, "time": timestamp, "cwd": cwd,
                 "command": srun_command, "state": state,
                 "exit_code": f"{result.returncode}:0", "allocation": bool(allocation),
                 "stdout": result.stdout or "", "stderr": result.stderr or ""})
        return result

    def record(conn):
        conn.execute(
            """
            insert or replace into jobs (
              job_id, submitted_at, submit_cwd, command, state, exit_code, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, timestamp, cwd, cslurm_command, state, f"{result.returncode}:0", timestamp, timestamp),
        )
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, command, cwd, raw_output)
            values (?, ?, ?, ?, ?, ?)
            """,
            (job_id, timestamp, state, srun_command, cwd, (result.stdout or "") + (result.stderr or "")),
        )

    run_write_transaction(record)

    return result


def main() -> None:
    result = run_job(os.sys.argv[1:], capture_output=False)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
