from datetime import datetime, timezone

from cslurm.ai.summarize import summarize_submission
from cslurm.config import ai_auto_summary_enabled
from cslurm.db import connect, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_auto_summary_failure(job_id: str, exc: Exception) -> None:
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, raw_output)
            values (?, ?, ?, ?)
            """,
            (job_id, _now(), "AI_SUMMARY_FAILED", f"{type(exc).__name__}: {exc}"),
        )
        conn.commit()


def auto_summarize_submission(job_id: str) -> str:
    if not ai_auto_summary_enabled():
        return "disabled"
    try:
        summarize_submission(job_id)
    except Exception as exc:
        record_auto_summary_failure(job_id, exc)
        return "failed"
    return "created"
