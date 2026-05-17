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


def list_events(job_id: str, limit: int = 50) -> str:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            select event_time, event_type, command, note, raw_output
            from job_events
            where job_id = ?
            order by id
            limit ?
            """,
            (job_id, limit),
        ).fetchall()
    return "\n".join(
        "\t".join(
            [
                row["event_time"] or "",
                row["event_type"] or "",
                row["command"] or "",
                row["note"] or "",
                (row["raw_output"] or "").strip(),
            ]
        )
        for row in rows
    )


def list_files(job_id: str, limit: int = 100) -> str:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            select relpath, role, source, size, copied, path
            from job_files
            where job_id = ?
            order by id
            limit ?
            """,
            (job_id, limit),
        ).fetchall()
    return "\n".join(
        f"{row['relpath'] or ''}\t{row['role'] or ''}\t{row['source'] or ''}\t{row['size'] or ''}\t{row['copied']}\t{row['path'] or ''}"
        for row in rows
    )


def list_commands(job_id: str, limit: int = 100) -> str:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            select time, hostname, cwd, kind, executable, argv, entry_file
            from job_commands
            where job_id = ?
            order by id
            limit ?
            """,
            (job_id, limit),
        ).fetchall()
    return "\n".join(
        f"{row['time'] or ''}\t{row['hostname'] or ''}\t{row['cwd'] or ''}\t{row['kind'] or ''}\t{row['executable'] or ''}\t{row['argv'] or ''}\t{row['entry_file'] or ''}"
        for row in rows
    )


def _tail_file(path: str, line_count: int) -> str:
    try:
        lines = open(path).read().splitlines()
    except FileNotFoundError:
        return f"<missing: {path}>"
    return "\n".join(lines[-line_count:])


def show_logs(job_id: str, tail: int = 200) -> str:
    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            "select stdout_path, stderr_path from jobs where job_id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"job not found: {job_id}")

    sections = []
    if row["stdout_path"]:
        sections.append("== stdout ==\n" + _tail_file(row["stdout_path"], tail))
    if row["stderr_path"]:
        sections.append("== stderr ==\n" + _tail_file(row["stderr_path"], tail))
    if not sections:
        return f"No stdout/stderr paths recorded for job {job_id}"
    return "\n".join(sections)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="aijobs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    show = subparsers.add_parser("show")
    show.add_argument("job_id")
    recent = subparsers.add_parser("recent")
    recent.add_argument("-n", "--limit", type=int, default=10)
    events = subparsers.add_parser("events")
    events.add_argument("job_id")
    events.add_argument("-n", "--limit", type=int, default=50)
    files = subparsers.add_parser("files")
    files.add_argument("job_id")
    files.add_argument("-n", "--limit", type=int, default=100)
    commands = subparsers.add_parser("commands")
    commands.add_argument("job_id")
    commands.add_argument("-n", "--limit", type=int, default=100)
    logs = subparsers.add_parser("logs")
    logs.add_argument("job_id")
    logs.add_argument("--tail", type=int, default=200)
    args = parser.parse_args()

    if args.command == "show":
        print(show_job(args.job_id))
    elif args.command == "recent":
        print(recent_jobs(args.limit))
    elif args.command == "events":
        print(list_events(args.job_id, args.limit))
    elif args.command == "files":
        print(list_files(args.job_id, args.limit))
    elif args.command == "commands":
        print(list_commands(args.job_id, args.limit))
    elif args.command == "logs":
        print(show_logs(args.job_id, args.tail))


if __name__ == "__main__":
    main()
