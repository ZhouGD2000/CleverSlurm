import json

from cslurm.ai.client import ModelClient
from cslurm.config import ai_max_tokens
from cslurm.db import connect, init_db


ASK_MAX_TOKENS = 768


def _decode_json(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _compact_text(value: str | None, *, limit: int = 500) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _recent_job_facts(conn, limit: int) -> list[dict]:
    jobs = conn.execute(
        """
        select job_id, submitted_at, submit_cwd, command, job_name, state, exit_code,
               elapsed, max_rss, nodelist, summary_json, completion_summary_json
        from jobs
        order by submitted_at desc
        limit ?
        """,
        (limit,),
    ).fetchall()
    facts = []
    for job in jobs:
        job_id = job["job_id"]
        events = conn.execute(
            """
            select event_time, event_type, note, raw_output
            from job_events
            where job_id = ? and event_type not like 'AI_%'
            order by id desc
            limit 10
            """,
            (job_id,),
        ).fetchall()
        commands = conn.execute(
            """
            select time, hostname, cwd, kind, executable, argv, entry_file
            from job_commands
            where job_id = ?
            order by id
            limit 10
            """,
            (job_id,),
        ).fetchall()
        facts.append(
            {
                "job_id": job_id,
                "submitted_at": job["submitted_at"],
                "job_name": job["job_name"],
                "state": job["state"],
                "exit_code": job["exit_code"],
                "elapsed": job["elapsed"],
                "max_rss": job["max_rss"],
                "nodelist": job["nodelist"],
                "submit_cwd": job["submit_cwd"],
                "command": job["command"],
                "summary": _decode_json(job["summary_json"]),
                "completion_summary": _decode_json(job["completion_summary_json"]),
                "recent_events": [
                    {
                        "event_time": row["event_time"],
                        "event_type": row["event_type"],
                        "note": _compact_text(row["note"]),
                        "raw_output": _compact_text(row["raw_output"]),
                    }
                    for row in events
                ],
                "runtime_commands": [{key: row[key] for key in row.keys()} for row in commands],
            }
        )
    return facts


def build_question_messages(question: str, facts: list[dict]) -> list[dict]:
    return build_text_question_messages(question, facts)


def build_text_question_messages(question: str, facts: list[dict]) -> list[dict]:
    payload = {
        "question": question,
        "recent_jobs": facts,
    }
    return [
        {
            "role": "system",
            "content": (
                "Answer questions about Slurm jobs using only the provided SQLite-derived facts. "
                "Do not invent jobs, paths, states, exit codes, or conclusions that are not supported by the facts. "
                "Answer in the same language as the user's question. Keep the answer concise. Do not output JSON."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _summary_text(value) -> str | None:
    if isinstance(value, dict):
        for key in ("human_summary", "one_line_summary", "summary", "title"):
            if value.get(key):
                return str(value[key])
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _job_description(fact: dict) -> str:
    return (
        _summary_text(fact.get("completion_summary"))
        or _summary_text(fact.get("summary"))
        or fact.get("command")
        or fact.get("job_name")
        or "unknown work"
    )


def _deterministic_answer(question: str, facts: list[dict], *, error: Exception | None = None) -> str:
    chinese = _contains_cjk(question)
    if not facts:
        if chinese:
            prefix = "AI 请求失败，" if error else ""
            return prefix + "没有找到最近任务记录。"
        prefix = "AI request failed; " if error else ""
        return prefix + "no recent job records were found."

    shown = facts[:10]
    if chinese:
        lines = []
        if error:
            lines.append(f"AI 请求失败，先根据本地记录给出确定性结果：最近记录了 {len(facts)} 个任务。")
        else:
            lines.append(f"根据本地记录，最近记录了 {len(facts)} 个任务。")
        for fact in shown:
            parts = [
                str(fact.get("job_id") or ""),
                f"名称 {fact.get('job_name')}" if fact.get("job_name") else None,
                f"状态 {fact.get('state')}" if fact.get("state") else None,
                f"提交时间 {fact.get('submitted_at')}" if fact.get("submitted_at") else None,
                f"工作内容：{_job_description(fact)}",
            ]
            lines.append("- " + "，".join(part for part in parts if part))
        return "\n".join(lines)

    lines = []
    if error:
        lines.append(f"AI request failed, so this is a deterministic answer from local records: {len(facts)} recent jobs.")
    else:
        lines.append(f"Local records contain {len(facts)} recent jobs.")
    for fact in shown:
        parts = [
            str(fact.get("job_id") or ""),
            f"name {fact.get('job_name')}" if fact.get("job_name") else None,
            f"state {fact.get('state')}" if fact.get("state") else None,
            f"submitted {fact.get('submitted_at')}" if fact.get("submitted_at") else None,
            f"work: {_job_description(fact)}",
        ]
        lines.append("- " + ", ".join(part for part in parts if part))
    return "\n".join(lines)


def _default_ask_client() -> ModelClient:
    return ModelClient(max_tokens=min(ai_max_tokens(), ASK_MAX_TOKENS), response_format=None)


def answer_question(
    question: str,
    *,
    client: ModelClient | None = None,
    limit: int = 10,
) -> str:
    client = client or _default_ask_client()
    with connect() as conn:
        init_db(conn)
        facts = _recent_job_facts(conn, limit)
    try:
        answer = client.chat_raw(build_text_question_messages(question, facts)).strip()
    except Exception as exc:
        return _deterministic_answer(question, facts, error=exc)
    if not isinstance(answer, str) or not answer.strip():
        return _deterministic_answer(question, facts)
    return answer
