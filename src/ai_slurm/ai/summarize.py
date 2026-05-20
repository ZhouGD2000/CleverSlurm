import json
from datetime import datetime, timezone

from ai_slurm.ai.client import SiliconFlowClient
from ai_slurm.ai.prompts import completion_messages, submission_messages
from ai_slurm.db import connect, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_summary_json(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("summary must be valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("summary must be a JSON object")
    return value


def _job_facts(conn, job_id: str) -> dict:
    job = conn.execute("select * from jobs where job_id = ?", (job_id,)).fetchone()
    if job is None:
        raise KeyError(f"job not found: {job_id}")
    files = conn.execute(
        "select path, relpath, size, role, source, copied, confidence from job_files where job_id = ? order by id limit 50",
        (job_id,),
    ).fetchall()
    events = conn.execute(
        "select event_time, event_type, note, raw_output from job_events where job_id = ? order by id desc limit 20",
        (job_id,),
    ).fetchall()
    return {
        "job": {key: job[key] for key in job.keys()},
        "files": [{key: row[key] for key in row.keys()} for row in files],
        "events": [{key: row[key] for key in row.keys()} for row in events],
    }


def summarize_submission(
    job_id: str,
    *,
    client: SiliconFlowClient | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool | None = None,
) -> dict:
    client = client or SiliconFlowClient(model=model, max_tokens=max_tokens, enable_thinking=enable_thinking)
    with connect() as conn:
        init_db(conn)
        facts = _job_facts(conn, job_id)
        content = client.chat_json(submission_messages(facts))
        summary = parse_summary_json(content)
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
    client: SiliconFlowClient | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    enable_thinking: bool | None = None,
) -> dict:
    client = client or SiliconFlowClient(model=model, max_tokens=max_tokens, enable_thinking=enable_thinking)
    with connect() as conn:
        init_db(conn)
        facts = _job_facts(conn, job_id)
        content = client.chat_json(completion_messages(facts))
        summary = parse_summary_json(content)
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
