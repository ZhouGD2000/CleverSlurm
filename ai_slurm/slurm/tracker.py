from datetime import datetime, timezone

from ai_slurm.db import connect, init_db
from ai_slurm.slurm.commands import run_slurm_command


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_sacct_table(output: str) -> dict[str, dict[str, str]]:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return {}
    headers = lines[0].split("|")
    rows = {}
    for line in lines[1:]:
        values = line.split("|")
        if len(values) != len(headers):
            continue
        row = dict(zip(headers, values))
        rows[row["JobID"]] = row
    return rows


def track_once() -> None:
    with connect() as conn:
        init_db(conn)
        jobs = conn.execute(
            "select job_id, state from jobs where state is null or state not in ('COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY')"
        ).fetchall()
        if not jobs:
            return

        job_ids = [job["job_id"] for job in jobs]
        result = run_slurm_command(
            "sacct",
            [
                "-P",
                "-n",
                "-j",
                ",".join(job_ids),
                "--format=JobID,State,ExitCode,Elapsed,MaxRSS,NodeList",
            ],
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)

        rows = _parse_sacct_table(result.stdout)
        timestamp = _now()
        for job in jobs:
            row = rows.get(job["job_id"])
            if not row:
                continue
            old_state = job["state"]
            new_state = row.get("State")
            conn.execute(
                """
                update jobs
                set state = ?, exit_code = ?, elapsed = ?, max_rss = ?, nodelist = ?, updated_at = ?
                where job_id = ?
                """,
                (
                    new_state,
                    row.get("ExitCode"),
                    row.get("Elapsed"),
                    row.get("MaxRSS"),
                    row.get("NodeList"),
                    timestamp,
                    job["job_id"],
                ),
            )
            if old_state != new_state:
                conn.execute(
                    """
                    insert into job_events (job_id, event_time, event_type, raw_output)
                    values (?, ?, ?, ?)
                    """,
                    (job["job_id"], timestamp, "STATE_CHANGED", f"{old_state} -> {new_state}"),
                )
        conn.commit()
