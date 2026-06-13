from datetime import datetime, timezone
import sys

from cslurm.ai.summarize import summarize_submission
from cslurm.config import ai_auto_summary_enabled
from cslurm.db import run_write_transaction


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_auto_summary_failure(job_id: str, exc: Exception) -> None:
    def record(conn):
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, raw_output)
            values (?, ?, ?, ?)
            """,
            (job_id, _now(), "AI_SUMMARY_FAILED", f"{type(exc).__name__}: {exc}"),
        )

    run_write_transaction(record)


def record_auto_summary_queued(job_id: str, *, pid: int | None = None) -> None:
    raw_output = f"pid={pid}" if pid is not None else None

    def record(conn):
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, raw_output)
            values (?, ?, ?, ?)
            """,
            (job_id, _now(), "AI_SUMMARY_QUEUED", raw_output),
        )

    run_write_transaction(record)


def auto_summarize_submission(job_id: str) -> str:
    if not ai_auto_summary_enabled():
        return "disabled"
    try:
        summarize_submission(job_id)
    except Exception as exc:
        record_auto_summary_failure(job_id, exc)
        return "failed"
    return "created"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m cslurm.ai.auto JOB_ID")
    auto_summarize_submission(sys.argv[1])


if __name__ == "__main__":
    main()
