import json
from datetime import datetime, timezone

from ai_slurm.config import root_dir


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _command_exists(conn, record: dict) -> bool:
    return (
        conn.execute(
            """
            select 1 from job_commands
            where job_id = ? and time = ? and hostname = ? and cwd = ? and kind = ?
              and executable = ? and argv = ?
            limit 1
            """,
            (
                record.get("job_id"),
                record.get("time"),
                record.get("hostname"),
                record.get("cwd"),
                record.get("kind"),
                record.get("executable"),
                json.dumps(record.get("argv", [])),
            ),
        ).fetchone()
        is not None
    )


def ingest_runtime_commands(conn, job_id: str) -> int:
    log_path = root_dir() / "jobs" / job_id / "runtime" / "commands.log"
    if not log_path.exists():
        return 0

    inserted = 0
    for line in log_path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        record["job_id"] = str(record.get("job_id") or job_id)
        argv_json = json.dumps(record.get("argv", []))
        if _command_exists(conn, record):
            continue
        conn.execute(
            """
            insert into job_commands (
              job_id, step_id, time, hostname, cwd, kind, executable, argv,
              entry_file, entry_file_abs, source
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["job_id"],
                record.get("step_id"),
                record.get("time"),
                record.get("hostname"),
                record.get("cwd"),
                record.get("kind"),
                record.get("executable"),
                argv_json,
                record.get("entry_file"),
                record.get("entry_file_abs"),
                "runtime-wrapper",
            ),
        )
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, raw_output)
            values (?, ?, ?, ?)
            """,
            (record["job_id"], record.get("time") or _now(), "COMMAND_EXECUTED", line),
        )
        inserted += 1
    conn.commit()
    return inserted
