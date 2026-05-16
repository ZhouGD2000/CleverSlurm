from ai_slurm.db import connect, init_db
from ai_slurm.cli.aijobs import list_commands, list_events, list_files, show_job


def test_aijobs_show_returns_job_metadata(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, job_name, state, exit_code, created_at, updated_at) "
            "values ('123456', '2026-05-17T01:02:03', '/work/project', 'aisbatch job.slurm', 'test-job', 'COMPLETED', '0:0', 't', 't')"
        )

    text = show_job("123456")

    assert "Job 123456" in text
    assert "state: COMPLETED" in text
    assert "job_name: test-job" in text
    assert "command: aisbatch job.slurm" in text


def test_aijobs_events_files_and_commands_return_tables(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into job_events (job_id, event_time, event_type, command, note, raw_output) "
            "values ('123456', '2026-05-17T01:02:03', 'CANCEL_REQUESTED', 'scancel 123456', 'wrong U', 'cancelled')"
        )
        conn.execute(
            "insert into job_files (job_id, path, relpath, sha256, size, role, source, copied, confidence) "
            "values ('123456', '/work/run.jl', 'run.jl', 'abc', 12, 'entry_file', 'runtime-command', 1, 1.0)"
        )
        conn.execute(
            "insert into job_commands (job_id, time, hostname, cwd, kind, executable, argv, entry_file, source) "
            "values ('123456', '2026-05-17T01:02:04', 'node001', '/work', 'julia', '/bin/julia', '[\"run.jl\"]', 'run.jl', 'runtime-wrapper')"
        )

    assert "CANCEL_REQUESTED" in list_events("123456")
    assert "wrong U" in list_events("123456")
    assert "run.jl" in list_files("123456")
    assert "entry_file" in list_files("123456")
    assert "julia" in list_commands("123456")
    assert "/bin/julia" in list_commands("123456")
