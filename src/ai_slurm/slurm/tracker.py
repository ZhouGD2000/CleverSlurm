from datetime import datetime, timezone

from ai_slurm.config import notification_auto_dispatch_enabled
from ai_slurm.db import connect, init_db
from ai_slurm.notify.dispatcher import process_job_completion
from ai_slurm.notify.feishu import dispatch_pending
from ai_slurm.runtime.ingest import ingest_runtime_commands, ingest_runtime_finish
from ai_slurm.slurm.commands import run_slurm_command


TERMINAL_STATES = {
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMEOUT",
    "OUT_OF_MEMORY",
    "NODE_FAIL",
    "BOOT_FAIL",
    "DEADLINE",
    "REVOKED",
    "SPECIAL_EXIT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_state(state: str | None) -> str:
    if not state:
        return "UNKNOWN"
    normalized = state.strip().upper()
    for sep in [" ", "+"]:
        if sep in normalized:
            normalized = normalized.split(sep, 1)[0]
    return normalized


def _parse_sacct_table(output: str) -> dict[str, dict[str, str]]:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines:
        return {}
    default_headers = ["JobID", "State", "ExitCode", "DerivedExitCode", "Elapsed", "MaxRSS", "NodeList"]
    if lines[0].startswith("JobID|"):
        headers = lines[0].split("|")
        data_lines = lines[1:]
    else:
        headers = default_headers
        data_lines = lines
    rows = {}
    for line in data_lines:
        values = line.split("|")
        if len(values) == 6:
            headers = ["JobID", "State", "ExitCode", "Elapsed", "MaxRSS", "NodeList"]
        if len(values) != len(headers):
            continue
        row = dict(zip(headers, values))
        if "." in row["JobID"]:
            continue
        rows[row["JobID"]] = row
    return rows


def track_once() -> None:
    should_dispatch = False
    with connect() as conn:
        init_db(conn)
        jobs = conn.execute(
            """
            select job_id, state
            from jobs
            where state is null or state not in (
              'COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT', 'OUT_OF_MEMORY',
              'NODE_FAIL', 'BOOT_FAIL', 'DEADLINE', 'REVOKED', 'SPECIAL_EXIT'
            )
            """
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
                "--format=JobID,State,ExitCode,DerivedExitCode,Elapsed,MaxRSS,NodeList",
            ],
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout)

        rows = _parse_sacct_table(result.stdout)
        timestamp = _now()
        for job in jobs:
            ingest_runtime_commands(conn, job["job_id"])
            ingest_runtime_finish(conn, job["job_id"])
            row = rows.get(job["job_id"])
            if not row:
                continue
            old_state = job["state"]
            new_state = row.get("State")
            conn.execute(
                """
                update jobs
                set state = ?, exit_code = ?, derived_exit_code = ?, elapsed = ?, max_rss = ?, nodelist = ?, updated_at = ?
                where job_id = ?
                """,
                (
                    new_state,
                    row.get("ExitCode"),
                    row.get("DerivedExitCode"),
                    row.get("Elapsed"),
                    row.get("MaxRSS"),
                    row.get("NodeList"),
                    timestamp,
                    job["job_id"],
                ),
            )
            if old_state != new_state:
                cursor = conn.execute(
                    """
                    insert into job_events (job_id, event_time, event_type, raw_output)
                    values (?, ?, ?, ?)
                    """,
                    (job["job_id"], timestamp, "STATE_CHANGED", f"{old_state} -> {new_state}"),
                )
                if _normalize_state(new_state) in TERMINAL_STATES:
                    try:
                        process_job_completion(conn, job["job_id"], event_id=int(cursor.lastrowid))
                        should_dispatch = True
                    except Exception as exc:
                        conn.execute(
                            """
                            insert into job_events (job_id, event_time, event_type, raw_output)
                            values (?, ?, ?, ?)
                            """,
                            (
                                job["job_id"],
                                timestamp,
                                "NOTIFICATION_ANALYSIS_FAILED",
                                f"{type(exc).__name__}: {exc}",
                            ),
                        )
        conn.commit()
    if should_dispatch and notification_auto_dispatch_enabled():
        dispatch_pending()
