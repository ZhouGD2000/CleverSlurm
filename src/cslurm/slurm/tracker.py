from datetime import datetime, timezone

from cslurm.config import notification_auto_dispatch_enabled
from cslurm.db import connect, init_db
from cslurm.notify.feishu import dispatch_pending
from cslurm.runtime.ingest import ingest_runtime_commands, ingest_runtime_finish
from cslurm.runtime.spool import ingest as ingest_srun_spool
from cslurm.runtime.cancellation import ingest as ingest_cancel_spool
from cslurm.slurm.commands import run_slurm_command


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


def _dispatch_due_notifications() -> None:
    if notification_auto_dispatch_enabled():
        dispatch_pending(mode="all")


def accounting_row(rows: dict[str, dict[str, str]], job_id: str) -> dict[str, str] | None:
    """sacct reports array elements, but submission records contain the parent ID."""
    if job_id in rows:
        return rows[job_id]
    children = [row for key, row in rows.items() if key.startswith(job_id + "_")]
    if not children:
        return None
    states = {_normalize_state(row.get("State")) for row in children}
    active = states - TERMINAL_STATES
    if active:
        state = next((s for s in ("RUNNING", "COMPLETING", "CONFIGURING", "PENDING") if s in active), sorted(active)[0])
    else:
        state = next((s for s in ("FAILED", "OUT_OF_MEMORY", "TIMEOUT", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "REVOKED", "SPECIAL_EXIT", "CANCELLED") if s in states), "COMPLETED")
    codes = {r.get("ExitCode", "") for r in children}
    return {"JobID": job_id, "State": state, "ExitCode": next(iter(codes)) if len(codes) == 1 else "",
            "DerivedExitCode": "", "Elapsed": "", "MaxRSS": "", "NodeList": ""}


def _track_once():
    from cslurm.work import enqueue, wake
    from cslurm.slurm.submissions import ingest as ingest_submissions
    ingest_submissions()
    with connect() as conn:
        init_db(conn)
        ingest_srun_spool(conn)
        ingest_cancel_spool(conn)
        jobs = conn.execute(
            "select job_id,state from jobs where state is null or state not in (" +
            ",".join("?" for _ in TERMINAL_STATES) + ")", tuple(TERMINAL_STATES)).fetchall()
        # Older accounting records can contain "CANCELLED by <uid>" or a suffix.
        jobs = [job for job in jobs if _normalize_state(job['state']) not in TERMINAL_STATES]
    rows = {}
    # Accounting calls never run while holding a database write transaction.
    for offset in range(0, len(jobs), 200):
        batch = jobs[offset:offset + 200]
        result = run_slurm_command("sacct", ["-P", "-n", "-j", ",".join(j["job_id"] for j in batch),
            "--format=JobID,State,ExitCode,DerivedExitCode,Elapsed,MaxRSS,NodeList"])
        if result.returncode != 0:
            raise RuntimeError("Slurm accounting request failed")
        rows.update(_parse_sacct_table(result.stdout))
    timestamp = _now()
    with connect() as conn:
        init_db(conn)
        for job in jobs:
            ingest_runtime_commands(conn, job["job_id"])
            ingest_runtime_finish(conn, job["job_id"])
            row = accounting_row(rows, job["job_id"])
            if not row:
                continue
            new_state = _normalize_state(row.get("State"))
            current = conn.execute("select state from jobs where job_id=?", (job["job_id"],)).fetchone()
            old_state = current["state"]
            conn.execute(
                "update jobs set state=?,exit_code=?,derived_exit_code=?,elapsed=?,max_rss=?,nodelist=?,updated_at=? where job_id=?",
                (new_state, row.get("ExitCode"), row.get("DerivedExitCode"), row.get("Elapsed"),
                 row.get("MaxRSS"), row.get("NodeList"), timestamp, job["job_id"]))
            if _normalize_state(old_state) != new_state:
                event_id = conn.execute(
                    "insert into job_events(job_id,event_time,event_type,raw_output) values (?,?,'STATE_CHANGED',?)",
                    (job["job_id"], timestamp, f"{old_state} -> {new_state}")).lastrowid
                if new_state in TERMINAL_STATES:
                    enqueue(conn, "completion", job["job_id"], event_id)
        conn.commit()
    # New transitions alone enqueue completion work. Never scan historical terminal jobs.
    wake()
    _dispatch_due_notifications()


def track_once() -> None:
    from cslurm.work import record_health
    record_health("tracker", "started_at")
    try:
        _track_once()
    except Exception as exc:
        record_health("tracker", "failed_at", type(exc).__name__)
        raise
    record_health("tracker", "succeeded_at")
