import json
import getpass
import os
from pathlib import Path

from cslurm.db import connect, init_db
from cslurm.slurm.sbatch_options import parse_cli_option, parse_command_args, parse_script_option


SQUEUE_STATE_CODES = {
    "BOOT_FAIL": "BF",
    "CANCELLED": "CA",
    "COMPLETED": "CD",
    "COMPLETING": "CG",
    "CONFIGURING": "CF",
    "DEADLINE": "DL",
    "FAILED": "F",
    "NODE_FAIL": "NF",
    "OUT_OF_MEMORY": "OOM",
    "PENDING": "PD",
    "PREEMPTED": "PR",
    "REQUEUED": "RQ",
    "RESIZING": "RS",
    "REVOKED": "RV",
    "RUNNING": "R",
    "SPECIAL_EXIT": "SE",
    "STAGE_OUT": "SO",
    "SUSPENDED": "S",
    "TIMEOUT": "TO",
    "UNKNOWN": "UN",
}


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


def show_summary(job_id: str, *, completion: bool = False) -> str:
    column = "completion_summary_json" if completion else "summary_json"
    label = "completion" if completion else "submission"
    with connect() as conn:
        init_db(conn)
        row = conn.execute(f"select {column} from jobs where job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError(f"job not found: {job_id}")

    value = row[column]
    if not value:
        return f"No {label} summary recorded for job {job_id}"
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return value
    return json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)


def _current_user() -> str:
    return os.environ.get("USER") or os.environ.get("LOGNAME") or getpass.getuser()


def _state_code(state: str | None) -> str:
    if not state:
        return "UN"
    normalized = state.strip().upper()
    for sep in [" ", "+"]:
        if sep in normalized:
            normalized = normalized.split(sep, 1)[0]
    return SQUEUE_STATE_CODES.get(normalized, normalized[:3] or "UN")


def _fit(value: object, width: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= width:
        return text
    return text[:width]


def _read_script_text(row) -> str | None:
    for key in ["original_script_path", "copied_script_path"]:
        path_text = row[key]
        if not path_text:
            continue
        try:
            return Path(path_text).expanduser().read_text(errors="replace")
        except OSError:
            continue
    return None


def _fallback_script_value(row, option: str, short_option: str) -> str | None:
    script_text = _read_script_text(row)
    if not script_text:
        return None
    return parse_script_option(script_text, option, short_option)


def _fallback_command_value(row, option: str, short_option: str) -> str | None:
    command = row["command"] if "command" in row.keys() else None
    if not command:
        return None
    return parse_cli_option(parse_command_args(command), option, short_option)


def _fallback_sbatch_value(row, option: str, short_option: str) -> str | None:
    return _fallback_command_value(row, option, short_option) or _fallback_script_value(row, option, short_option)


def _nodelist_or_reason(row) -> str:
    if row["nodelist"]:
        return row["nodelist"]
    if row["reason"]:
        reason = str(row["reason"])
        return reason if reason.startswith("(") and reason.endswith(")") else f"({reason})"
    return ""


def _squeue_header() -> str:
    return (
        f"{'JOBID':>18} {'PARTITION':<9} {'NAME':<8} {'USER':<8} "
        f"{'ST':<3} {'TIME':>10} {'NODES':>6} NODELIST(REASON)"
    )


def _squeue_row(row) -> str:
    partition = row["partition"] or _fallback_sbatch_value(row, "partition", "-p") or "*"
    job_name = row["job_name"] or _fallback_sbatch_value(row, "job-name", "-J") or ""
    return (
        f"{str(row['job_id'] or ''):>18} "
        f"{_fit(partition, 9):<9} "
        f"{_fit(job_name, 8):<8} "
        f"{_fit(row['user'] or _current_user(), 8):<8} "
        f"{_state_code(row['state']):<3} "
        f"{_fit(row['elapsed'] or '0:00', 10):>10} "
        f"{_fit(row['nodes'] or '', 6):>6} "
        f"{_nodelist_or_reason(row)}"
    ).rstrip()


def recent_jobs(limit: int = 10, *, no_header: bool = False) -> str:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            select job_id, partition, job_name, user, state, elapsed, nodes, nodelist, reason,
                   original_script_path, copied_script_path, command
            from jobs
            order by submitted_at desc
            limit ?
            """,
            (limit,),
        ).fetchall()
    lines = [] if no_header else [_squeue_header()]
    lines.extend(_squeue_row(row) for row in rows)
    return "\n".join(lines)


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
        job = conn.execute(
            "select original_script_path, copied_script_path from jobs where job_id = ?",
            (job_id,),
        ).fetchone()
    if not rows and job:
        fallback_rows = []
        if job["original_script_path"]:
            path = Path(job["original_script_path"])
            fallback_rows.append(
                {
                    "relpath": "original.slurm",
                    "role": "original_script",
                    "source": "jobs",
                    "size": path.stat().st_size if path.exists() else "",
                    "copied": 0,
                    "path": str(path),
                }
            )
        if job["copied_script_path"]:
            path = Path(job["copied_script_path"])
            fallback_rows.append(
                {
                    "relpath": "instrumented.slurm",
                    "role": "instrumented_script",
                    "source": "jobs",
                    "size": path.stat().st_size if path.exists() else "",
                    "copied": 1,
                    "path": str(path),
                }
            )
        rows = fallback_rows
    if not rows:
        return f"No files recorded for job {job_id}"
    return "\n".join(
        f"{row['relpath'] or ''}\t{row['role'] or ''}\t{row['source'] or ''}\t{row['size'] or ''}\t{row['copied']}\t{row['path'] or ''}"
        for row in rows
    )


def list_commands(job_id: str, limit: int = 100) -> str:
    with connect() as conn:
        init_db(conn)
        rows = conn.execute(
            """
            select time, hostname, cwd, kind, executable, argv, entry_file, source
            from job_commands
            where job_id = ?
            order by id
            limit ?
            """,
            (job_id, limit),
        ).fetchall()
    if not rows:
        return f"No commands recorded for job {job_id}"
    return "\n".join(
        f"{row['time'] or ''}\t{row['hostname'] or ''}\t{row['cwd'] or ''}\t{row['kind'] or ''}\t{row['executable'] or ''}\t{row['argv'] or ''}\t{row['entry_file'] or ''}\t{row['source'] or ''}"
        for row in rows
    )


def list_notifications(job_id: str | None = None, limit: int = 50) -> str:
    with connect() as conn:
        init_db(conn)
        if job_id:
            rows = conn.execute(
                """
                select id, created_at, job_id, mode, status, severity, category, title, last_error
                from notifications
                where job_id = ?
                order by id desc
                limit ?
                """,
                (job_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select id, created_at, job_id, mode, status, severity, category, title, last_error
                from notifications
                order by id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
    if not rows:
        return f"No notifications recorded for job {job_id}" if job_id else "No notifications recorded"
    return "\n".join(
        "\t".join(
            [
                str(row["id"]),
                row["created_at"] or "",
                row["job_id"] or "",
                row["mode"] or "",
                row["status"] or "",
                row["severity"] or "",
                row["category"] or "",
                row["title"] or "",
                row["last_error"] or "",
            ]
        )
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
            "select stdout_path, stderr_path, submit_cwd from jobs where job_id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        raise KeyError(f"job not found: {job_id}")

    sections = []
    if row["stdout_path"]:
        sections.append("== stdout ==\n" + _tail_file(row["stdout_path"], tail))
    if row["stderr_path"]:
        sections.append("== stderr ==\n" + _tail_file(row["stderr_path"], tail))
    if not sections and row["submit_cwd"]:
        default_log = Path(row["submit_cwd"]) / f"slurm-{job_id}.out"
        if default_log.exists():
            sections.append("== stdout (inferred default slurm log) ==\n" + _tail_file(str(default_log), tail))
    if not sections:
        return f"No stdout/stderr paths recorded for job {job_id}"
    return "\n".join(sections)


def main() -> None:
    import argparse
    from cslurm.ai.ask import answer_question

    parser = argparse.ArgumentParser(prog="cjobs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    show = subparsers.add_parser("show")
    show.add_argument("job_id")
    summary = subparsers.add_parser("summary")
    summary.add_argument("job_id")
    summary.add_argument("--completion", action="store_true")
    recent = subparsers.add_parser("recent")
    recent.add_argument("-n", "--limit", type=int, default=10)
    recent.add_argument("--no-header", action="store_true")
    events = subparsers.add_parser("events")
    events.add_argument("job_id")
    events.add_argument("-n", "--limit", type=int, default=50)
    files = subparsers.add_parser("files")
    files.add_argument("job_id")
    files.add_argument("-n", "--limit", type=int, default=100)
    commands = subparsers.add_parser("commands")
    commands.add_argument("job_id")
    commands.add_argument("-n", "--limit", type=int, default=100)
    notifications = subparsers.add_parser("notifications")
    notifications.add_argument("job_id", nargs="?")
    notifications.add_argument("-n", "--limit", type=int, default=50)
    logs = subparsers.add_parser("logs")
    logs.add_argument("job_id")
    logs.add_argument("--tail", type=int, default=200)
    ask = subparsers.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("-n", "--limit", type=int, default=10)
    args = parser.parse_args()

    if args.command == "show":
        print(show_job(args.job_id))
    elif args.command == "summary":
        print(show_summary(args.job_id, completion=args.completion))
    elif args.command == "recent":
        print(recent_jobs(args.limit, no_header=args.no_header))
    elif args.command == "events":
        print(list_events(args.job_id, args.limit))
    elif args.command == "files":
        print(list_files(args.job_id, args.limit))
    elif args.command == "commands":
        print(list_commands(args.job_id, args.limit))
    elif args.command == "notifications":
        print(list_notifications(args.job_id, args.limit))
    elif args.command == "logs":
        print(show_logs(args.job_id, args.tail))
    elif args.command == "ask":
        print(answer_question(args.question, limit=args.limit))


if __name__ == "__main__":
    main()
