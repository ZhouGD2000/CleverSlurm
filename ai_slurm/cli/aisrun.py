import os
import subprocess
from datetime import datetime, timezone

from ai_slurm.config import command_path
from ai_slurm.db import connect, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_job_id() -> str:
    return "local-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")


def run_job(argv: list[str]) -> subprocess.CompletedProcess[str]:
    if not argv:
        raise SystemExit("usage: aisrun [srun-args...] command [args...]")

    job_id = os.environ.get("SLURM_JOB_ID") or _local_job_id()
    timestamp = _now()
    cwd = os.getcwd()
    ai_command = "aisrun " + " ".join(argv)
    srun_command = "srun " + " ".join(argv)
    result = subprocess.run(
        [command_path("srun"), *argv],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    state = "COMPLETED" if result.returncode == 0 else "FAILED"

    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert or replace into jobs (
              job_id, submitted_at, submit_cwd, command, state, exit_code, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, timestamp, cwd, ai_command, state, f"{result.returncode}:0", timestamp, timestamp),
        )
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, command, cwd, raw_output)
            values (?, ?, ?, ?, ?, ?)
            """,
            (job_id, timestamp, state, srun_command, cwd, result.stdout + result.stderr),
        )
        conn.commit()

    return result


def main() -> None:
    result = run_job(os.sys.argv[1:])
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=os.sys.stderr)
    raise SystemExit(result.returncode)
