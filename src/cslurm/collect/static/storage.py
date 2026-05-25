import json
from pathlib import Path

from cslurm.collect.static.common import StaticCommand, StaticInsertResult
from cslurm.collect.static.script import find_static_commands


def _command_exists(conn, *, job_id: str, command: StaticCommand, argv_json: str) -> bool:
    row = conn.execute(
        """
        select 1 from job_commands
        where job_id = ?
          and source = 'static-script'
          and kind = ?
          and executable = ?
          and argv = ?
          and coalesce(entry_file, '') = coalesce(?, '')
          and coalesce(entry_file_abs, '') = coalesce(?, '')
        limit 1
        """,
        (job_id, command.kind, command.executable, argv_json, command.entry_file, command.entry_file_abs),
    ).fetchone()
    return row is not None


def _job_file_exists(conn, *, job_id: str, path: str, role: str, source: str) -> bool:
    row = conn.execute(
        """
        select 1 from job_files
        where job_id = ? and path = ? and role = ? and source = ?
        limit 1
        """,
        (job_id, path, role, source),
    ).fetchone()
    return row is not None


def insert_static_commands(conn, *, job_id: str, script_text: str, script_dir: Path, timestamp: str) -> StaticInsertResult:
    inserted_commands = 0
    inserted_files = 0
    seen_entries = set()
    for command in find_static_commands(script_text, script_dir):
        argv_json = json.dumps(command.argv)
        if not _command_exists(conn, job_id=job_id, command=command, argv_json=argv_json):
            conn.execute(
                """
                insert into job_commands (
                  job_id, step_id, time, hostname, cwd, kind, executable, argv,
                  entry_file, entry_file_abs, source
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    None,
                    timestamp,
                    None,
                    str(script_dir),
                    command.kind,
                    command.executable,
                    argv_json,
                    command.entry_file,
                    command.entry_file_abs,
                    "static-script",
                ),
            )
            inserted_commands += 1
        if command.entry_file_abs and command.entry_file_abs not in seen_entries:
            path = Path(command.entry_file_abs)
            if _job_file_exists(conn, job_id=job_id, path=str(path), role="entry_file", source="static-script"):
                seen_entries.add(command.entry_file_abs)
                continue
            conn.execute(
                """
                insert into job_files (job_id, path, relpath, size, role, source, copied, confidence)
                values (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    str(path),
                    path.name,
                    path.stat().st_size if path.exists() else None,
                    "entry_file",
                    "static-script",
                    0,
                    0.8,
                ),
            )
            inserted_files += 1
            seen_entries.add(command.entry_file_abs)
    return StaticInsertResult(commands=inserted_commands, files=inserted_files)
