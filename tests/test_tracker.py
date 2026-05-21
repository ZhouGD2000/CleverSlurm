import sqlite3

from conftest import write_executable


def test_tracker_updates_state_from_fake_sacct_and_records_state_change(isolated_home, fake_bin):
    from cslurm.db import connect, init_db
    from cslurm.slurm.tracker import track_once

    write_executable(
        fake_bin / "sacct",
        "#!/bin/sh\nprintf 'JobID|State|ExitCode|Elapsed|MaxRSS|NodeList\\n123456|COMPLETED|0:0|00:00:03|12M|node001\\n'\n",
    )

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, state, created_at, updated_at) "
            "values ('123456', 't', '/tmp', 'csbatch job.slurm', 'UNKNOWN', 't', 't')"
        )

    track_once()

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        job = conn.execute(
            "select state, exit_code, elapsed, max_rss, nodelist from jobs where job_id = '123456'"
        ).fetchone()
        event = conn.execute(
            "select event_type, raw_output from job_events where job_id = '123456'"
        ).fetchone()

    assert job == ("COMPLETED", "0:0", "00:00:03", "12M", "node001")
    assert event[0] == "STATE_CHANGED"
    assert "UNKNOWN -> COMPLETED" in event[1]


def test_tracker_parses_real_sacct_no_header_output(isolated_home, fake_bin):
    from cslurm.db import connect, init_db
    from cslurm.slurm.tracker import track_once

    write_executable(
        fake_bin / "sacct",
        "#!/bin/sh\nprintf '123456|COMPLETED|0:0|00:00:03|12M|node001\\n123456.batch|COMPLETED|0:0|00:00:03|12M|node001\\n'\n",
    )

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, state, created_at, updated_at) "
            "values ('123456', 't', '/tmp', 'csbatch job.slurm', 'UNKNOWN', 't', 't')"
        )

    track_once()

    with connect() as conn:
        job = conn.execute(
            "select state, exit_code, elapsed, max_rss, nodelist from jobs where job_id = '123456'"
        ).fetchone()

    assert tuple(job) == ("COMPLETED", "0:0", "00:00:03", "12M", "node001")
