from ai_slurm.db import connect, init_db


def show_job(job_id: str) -> str:
    with connect() as conn:
        init_db(conn)
        row = conn.execute("select * from jobs where job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"job not found: {job_id}")

    lines = [f"Job {row['job_id']}"]
    for key in ["job_name", "state", "exit_code", "submitted_at", "submit_cwd", "command"]:
        value = row[key]
        if value is not None:
            lines.append(f"{key}: {value}")
    return "\n".join(lines)


def recent_jobs(limit: int = 10) -> str:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            "select job_id, state, job_name, command from jobs order by submitted_at desc limit ?",
            (limit,),
        ).fetchall()
    return "\n".join(
        f"{row['job_id']}\t{row['state'] or ''}\t{row['job_name'] or ''}\t{row['command'] or ''}"
        for row in rows
    )
