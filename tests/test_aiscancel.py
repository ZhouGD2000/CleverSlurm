import sqlite3

from conftest import write_executable


def test_aiscancel_calls_fake_scancel_and_records_note(isolated_home, fake_bin):
    from ai_slurm.db import connect, init_db
    from ai_slurm.cli.aiscancel import cancel_job

    write_executable(
        fake_bin / "scancel",
        "#!/bin/sh\nprintf 'cancelled %s\\n' \"$1\"\n",
    )

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, state, created_at, updated_at) "
            "values ('123456', 't', '/tmp', 'aisbatch job.slurm', 'RUNNING', 't', 't')"
        )

    cancel_job(["123456", "--note", "Wrong parameter U"])

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        event = conn.execute(
            "select event_type, command, note, raw_output from job_events where job_id = '123456'"
        ).fetchone()

    assert event == (
        "CANCEL_REQUESTED",
        "scancel 123456",
        "Wrong parameter U",
        "cancelled 123456\n",
    )
