import json

from ai_slurm.ai.client import ModelClient
from ai_slurm.ai.summarize import parse_summary_json
from ai_slurm.db import connect, init_db


ASK_SYSTEM_PROMPT = """\
You answer questions about Slurm jobs using only the provided SQLite-derived facts.
Do not invent jobs, paths, states, exit codes, or conclusions that are not supported by the facts.
Answer in the same language as the user's question. When useful, group jobs by status or purpose.
Return one JSON object with an "answer" string.
"""


def _decode_json(value: str | None):
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


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
            where job_id = ?
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
                "recent_events": [{key: row[key] for key in row.keys()} for row in events],
                "runtime_commands": [{key: row[key] for key in row.keys()} for row in commands],
            }
        )
    return facts


def build_question_messages(question: str, facts: list[dict]) -> list[dict]:
    payload = {
        "question": question,
        "recent_jobs": facts,
    }
    return [
        {"role": "system", "content": ASK_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=2)},
    ]


def answer_question(
    question: str,
    *,
    client: ModelClient | None = None,
    limit: int = 10,
) -> str:
    client = client or ModelClient()
    with connect() as conn:
        init_db(conn)
        facts = _recent_job_facts(conn, limit)
    content = client.chat_json(build_question_messages(question, facts))
    parsed = parse_summary_json(content)
    answer = parsed.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("AI answer JSON must contain a non-empty 'answer' string")
    return answer
