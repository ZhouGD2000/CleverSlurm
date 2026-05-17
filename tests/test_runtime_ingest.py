import json
import sqlite3

from ai_slurm.db import connect, init_db
from ai_slurm.runtime.ingest import ingest_runtime_commands, ingest_runtime_finish


def test_ingest_runtime_commands_imports_jsonl_and_is_idempotent(isolated_home):
    runtime_dir = isolated_home / "jobs" / "123456" / "runtime"
    runtime_dir.mkdir(parents=True)
    command = {
        "time": "2026-05-17T01:02:03Z",
        "job_id": "123456",
        "hostname": "node001",
        "cwd": "/work/project",
        "kind": "julia",
        "executable": "/opt/julia/bin/julia",
        "argv": ["run.jl", "--U", "4"],
        "entry_file": "run.jl",
    }
    (runtime_dir / "commands.log").write_text(json.dumps(command) + "\n")

    with connect() as conn:
        init_db(conn)
        first = ingest_runtime_commands(conn, "123456")
        second = ingest_runtime_commands(conn, "123456")

    assert first == 1
    assert second == 0
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute(
            "select job_id, hostname, cwd, kind, executable, argv, entry_file, source from job_commands"
        ).fetchone()
        event_count = conn.execute(
            "select count(*) from job_events where job_id = '123456' and event_type = 'COMMAND_EXECUTED'"
        ).fetchone()[0]

    assert row == (
        "123456",
        "node001",
        "/work/project",
        "julia",
        "/opt/julia/bin/julia",
        '["run.jl", "--U", "4"]',
        "run.jl",
        "runtime-wrapper",
    )
    assert event_count == 1


def test_ingest_runtime_finish_records_program_finished_event_once(isolated_home):
    runtime_dir = isolated_home / "jobs" / "123456" / "runtime"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "finish.log").write_text(
        '{"time":"2026-05-17T01:02:05Z","job_id":"123456","hostname":"node001","exit_code":0,"event_type":"PROGRAM_FINISHED"}\n'
    )

    with connect() as conn:
        init_db(conn)
        first = ingest_runtime_finish(conn, "123456")
        second = ingest_runtime_finish(conn, "123456")

    assert first == 1
    assert second == 0
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        event = conn.execute(
            "select event_type, raw_output from job_events where job_id = '123456'"
        ).fetchone()

    assert event[0] == "PROGRAM_FINISHED"
    assert '"exit_code":0' in event[1]
