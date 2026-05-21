import json
from datetime import datetime, timezone

from ai_slurm.ai.client import ModelClient
from ai_slurm.ai.json_utils import parse_json_object
from ai_slurm.ai.prompts import completion_messages, submission_messages
from ai_slurm.db import connect, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_text(value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def parse_summary_json(text: str) -> dict:
    return parse_json_object(text, error_label="summary")


def _fallback_summary(job_id: str, content: str, *, kind: str) -> dict:
    key = "human_summary" if kind == "completion" else "one_line_summary"
    return {
        "job_id": job_id,
        key: content.strip(),
        "raw_ai_summary": content.strip(),
        "summary_mode": "text_fallback",
        "summary_confidence": 0.3,
    }


def _text_summary_messages(facts: dict, *, kind: str) -> list[dict]:
    job = facts.get("job") or {}
    events = facts.get("events") or []
    compact = {
        "job_id": job.get("job_id"),
        "job_name": job.get("job_name"),
        "state": job.get("state"),
        "exit_code": job.get("exit_code"),
        "derived_exit_code": job.get("derived_exit_code"),
        "elapsed": job.get("elapsed"),
        "command": job.get("command"),
        "submit_cwd": job.get("submit_cwd"),
        "recent_events": events[:5],
    }
    if kind == "completion":
        instruction = "Summarize this Slurm job completion in one concise sentence. Do not output JSON."
    else:
        instruction = "Summarize this Slurm job submission in one concise sentence. Do not output JSON."
    return [
        {"role": "system", "content": instruction},
        {"role": "user", "content": json.dumps(compact, ensure_ascii=False, sort_keys=True)},
    ]


def _request_summary(client: ModelClient, job_id: str, facts: dict, *, kind: str) -> tuple[dict, str]:
    messages = completion_messages(facts) if kind == "completion" else submission_messages(facts)
    try:
        content = client.chat_json(messages)
        return parse_summary_json(content), content
    except Exception:
        content = client.chat_raw(_text_summary_messages(facts, kind=kind))
        return _fallback_summary(job_id, content, kind=kind), content


def _job_facts(conn, job_id: str) -> dict:
    job = conn.execute("select * from jobs where job_id = ?", (job_id,)).fetchone()
    if job is None:
        raise KeyError(f"job not found: {job_id}")
    files = conn.execute(
        "select path, relpath, size, role, source, copied, confidence from job_files where job_id = ? order by id limit 50",
        (job_id,),
    ).fetchall()
    events = conn.execute(
        """
        select event_time, event_type, note, raw_output
        from job_events
        where job_id = ? and event_type not like 'AI_%'
        order by id desc
        limit 20
        """,
        (job_id,),
    ).fetchall()
    return {
        "job": {key: job[key] for key in job.keys()},
        "files": [{key: row[key] for key in row.keys()} for row in files],
        "events": [
            {
                "event_time": row["event_time"],
                "event_type": row["event_type"],
                "note": _compact_text(row["note"]),
                "raw_output": _compact_text(row["raw_output"]),
            }
            for row in events
        ],
    }


def summarize_submission(
    job_id: str,
    *,
    client: ModelClient | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool | None = None,
) -> dict:
    client = client or ModelClient(model=model, max_tokens=max_tokens, enable_thinking=enable_thinking)
    with connect() as conn:
        init_db(conn)
        facts = _job_facts(conn, job_id)
        summary, content = _request_summary(client, job_id, facts, kind="submission")
        timestamp = _now()
        conn.execute(
            "update jobs set summary_json = ?, updated_at = ? where job_id = ?",
            (json.dumps(summary, ensure_ascii=False, sort_keys=True), timestamp, job_id),
        )
        conn.execute(
            "insert into job_events (job_id, event_time, event_type, raw_output) values (?, ?, ?, ?)",
            (job_id, timestamp, "AI_SUMMARY_CREATED", content),
        )
        conn.commit()
        return summary


def summarize_completion(
    job_id: str,
    *,
    client: ModelClient | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool | None = None,
) -> dict:
    client = client or ModelClient(model=model, max_tokens=max_tokens, enable_thinking=enable_thinking)
    with connect() as conn:
        init_db(conn)
        facts = _job_facts(conn, job_id)
        summary, content = _request_summary(client, job_id, facts, kind="completion")
        timestamp = _now()
        conn.execute(
            "update jobs set completion_summary_json = ?, updated_at = ? where job_id = ?",
            (json.dumps(summary, ensure_ascii=False, sort_keys=True), timestamp, job_id),
        )
        conn.execute(
            "insert into job_events (job_id, event_time, event_type, raw_output) values (?, ?, ?, ?)",
            (job_id, timestamp, "AI_COMPLETION_SUMMARY_CREATED", content),
        )
        conn.commit()
        return summary
