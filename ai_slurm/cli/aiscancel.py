import argparse
import os
from datetime import datetime, timezone

from ai_slurm.db import connect, init_db
from ai_slurm.slurm.commands import run_slurm_command


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cancel_job(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(prog="aiscancel")
    parser.add_argument("job_id")
    parser.add_argument("--note")
    args = parser.parse_args(argv)

    result = run_slurm_command("scancel", [args.job_id])
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, command, cwd, note, raw_output)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                args.job_id,
                _now(),
                "CANCEL_REQUESTED",
                f"scancel {args.job_id}",
                os.getcwd(),
                args.note,
                result.stdout,
            ),
        )
        conn.commit()


def main() -> None:
    cancel_job(os.sys.argv[1:])


if __name__ == "__main__":
    main()
