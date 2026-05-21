import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from cslurm.config import notification_ai_analysis_enabled
from cslurm.notify.analysis import analyze_job, record_job_analysis
from cslurm.notify.semantic import merge_ai_analysis, request_ai_semantic_analysis, should_run_ai_analysis


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def notification_dedupe_key(analysis: dict[str, Any]) -> str:
    status = analysis.get("semantic_status") or analysis.get("deterministic_status") or "unknown"
    category = analysis.get("failure_category") or "UNKNOWN"
    return f"job:{analysis['job_id']}:{status}:{category}"


def enqueue_job_notification(conn, analysis: dict[str, Any], event_id: int | None = None) -> int:
    dedupe_key = notification_dedupe_key(analysis)
    existing = conn.execute("select id from notifications where dedupe_key = ?", (dedupe_key,)).fetchone()
    if existing:
        return int(existing["id"])

    mode = analysis.get("recommended_notification") or "batch"
    status = "suppressed" if mode == "silent" else "pending"
    payload = {
        "job_id": analysis["job_id"],
        "title": analysis.get("title"),
        "body": analysis.get("body"),
        "analysis": {
            "deterministic_status": analysis.get("deterministic_status"),
            "semantic_status": analysis.get("semantic_status"),
            "failure_category": analysis.get("failure_category"),
            "severity": analysis.get("severity"),
            "confidence": analysis.get("confidence"),
        },
    }
    try:
        cursor = conn.execute(
            """
            insert into notifications (
              job_id, event_id, group_id, created_at, severity, category, channel,
              mode, title, body, payload_json, status, dedupe_key
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis["job_id"],
                event_id,
                analysis.get("group_id"),
                _now(),
                analysis.get("severity"),
                analysis.get("failure_category"),
                "feishu",
                mode,
                analysis.get("title"),
                analysis.get("body"),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                status,
                dedupe_key,
            ),
        )
    except sqlite3.IntegrityError:
        existing = conn.execute("select id from notifications where dedupe_key = ?", (dedupe_key,)).fetchone()
        if existing:
            return int(existing["id"])
        raise
    return int(cursor.lastrowid)


def process_job_completion(conn, job_id: str, event_id: int | None = None) -> int:
    analysis = analyze_job(conn, job_id)
    ai_analysis = None
    if notification_ai_analysis_enabled() and should_run_ai_analysis(analysis):
        try:
            ai_analysis = request_ai_semantic_analysis(conn, job_id, analysis)
            analysis = merge_ai_analysis(analysis, ai_analysis)
        except Exception as exc:
            conn.execute(
                """
                insert into job_events (job_id, event_time, event_type, raw_output)
                values (?, ?, ?, ?)
                """,
                (job_id, _now(), "AI_LOG_ANALYSIS_FAILED", f"{type(exc).__name__}: {exc}"),
            )
    record_job_analysis(conn, analysis, ai_analysis=ai_analysis)
    return enqueue_job_notification(conn, analysis, event_id=event_id)


def pending_notifications(conn, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        select *
        from notifications
        where status = 'pending'
        order by id
        limit ?
        """,
        (limit,),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]
