"""Run native srun with inherited terminal streams and best-effort receipts."""

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone

from cslurm.slurm import native
from cslurm.slurm.sbatch_options import command_text


def run_job(argv: list[str], *, capture_output: bool = True, defer: bool = False) -> subprocess.CompletedProcess[str]:
    timestamp = datetime.now(timezone.utc).isoformat()
    result = native.run("srun", argv, capture_output=capture_output)
    try:
        from cslurm.runtime.spool import publish, ingest
        from cslurm.db import connect, init_db
        # These forms report the client, not an application execution.
        if argv in (["--help"], ["-h"], ["--usage"], ["--version"], ["-V"]) or not argv:
            return result
        allocation = os.environ.get("SLURM_JOB_ID") or os.environ.get("SLURM_JOBID")
        job_id = allocation or "local-" + uuid.uuid4().hex
        if allocation and os.environ.get("SLURM_ARRAY_JOB_ID") and os.environ.get("SLURM_ARRAY_TASK_ID"):
            job_id = os.environ["SLURM_ARRAY_JOB_ID"] + "_" + os.environ["SLURM_ARRAY_TASK_ID"]
        path = publish({
            "job_id": job_id, "time": datetime.now(timezone.utc).isoformat(),
            "started_at": timestamp, "cwd": os.getcwd(),
            "command": command_text("srun", argv), "wrapper_command": command_text("csrun", argv),
            "state": "COMPLETED" if result.returncode == 0 else "FAILED",
            "exit_code": f"{result.returncode}:0" if result.returncode >= 0 else f"0:{-result.returncode}",
            "allocation": bool(allocation), "returncode": result.returncode,
            "stdout": result.stdout or "", "stderr": result.stderr or "",
        })
        if not allocation:
            if defer:
                from cslurm.work import wake
                wake()
            else:
                with connect() as conn:
                    init_db(conn)
                    ingest(conn, paths=[path])
    except Exception:
        # Tracking must not change a successful execution or its native failure.
        pass
    return result


def main() -> None:
    try:
        if os.environ.get("CSLURM_TRACK_COMMANDS", "true").lower() in {"false", "0", "off"}:
            native.exec_native("srun", sys.argv[1:])
        result = run_job(sys.argv[1:], capture_output=False, defer=True)
    except OSError as exc:
        print(f"csrun: {exc}", file=sys.stderr)
        raise SystemExit(127 if isinstance(exc, FileNotFoundError) else 126)
    native.exit_like_native(result.returncode)


if __name__ == "__main__":
    main()
