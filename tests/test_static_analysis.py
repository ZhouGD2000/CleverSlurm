import sqlite3


def test_static_analysis_worker_records_commands_entries_and_log_paths(isolated_home, tmp_path):
    from cslurm.collect.static_auto import static_analyze_submission
    from cslurm.db import connect, init_db

    (tmp_path / "Dinf.m").write_text("disp('run')\n")
    (tmp_path / "run.py").write_text("print('run')\n")
    script = tmp_path / "job.slurm"
    script.write_text(
        "#!/bin/bash\n"
        "EXE=/home/software/MATLAB/R2022b/bin/matlab\n"
        "$EXE -nodisplay -r Dinf;exit;\n"
        "PY=/usr/bin/python3\n"
        "$PY run.py --alpha 1\n"
    )

    job_dir = isolated_home / "jobs" / "123456"
    job_dir.mkdir(parents=True)
    (job_dir / "original.slurm").write_text(script.read_text())
    stdout_path = tmp_path / "slurm-123456.out"

    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, submit_cwd, original_script_path,
              copied_script_path, stdout_path, stderr_path, state, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "123456",
                "2026-05-22T00:00:00+00:00",
                str(tmp_path),
                str(script),
                str(job_dir / "instrumented.slurm"),
                str(stdout_path),
                str(stdout_path),
                "UNKNOWN",
                "t",
                "t",
            ),
        )
        conn.commit()

    assert static_analyze_submission("123456") == "created"

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        commands = conn.execute(
            """
            select kind, executable, argv, entry_file, entry_file_abs, source
            from job_commands where job_id = '123456' order by kind
            """
        ).fetchall()
        files = conn.execute(
            """
            select relpath, role, source, copied, path
            from job_files where job_id = '123456' order by role, relpath
            """
        ).fetchall()
        event = conn.execute(
            """
            select event_type, raw_output from job_events
            where job_id = '123456' and event_type = 'STATIC_ANALYSIS_CREATED'
            """
        ).fetchone()

    assert commands == [
        (
            "matlab",
            "/home/software/MATLAB/R2022b/bin/matlab",
            '["-nodisplay", "-r", "Dinf"]',
            "Dinf.m",
            str(tmp_path / "Dinf.m"),
            "static-script",
        ),
        (
            "python",
            "/usr/bin/python3",
            '["run.py", "--alpha", "1"]',
            "run.py",
            str(tmp_path / "run.py"),
            "static-script",
        ),
    ]
    assert ("Dinf.m", "entry_file", "static-script", 0, str(tmp_path / "Dinf.m")) in files
    assert ("run.py", "entry_file", "static-script", 0, str(tmp_path / "run.py")) in files
    assert ("slurm-123456.out", "stdout", "sbatch", 0, str(stdout_path)) in files
    assert event == ("STATIC_ANALYSIS_CREATED", "commands=2 files=3")


def test_static_analysis_worker_is_idempotent(isolated_home, tmp_path):
    from cslurm.collect.static_auto import static_analyze_submission
    from cslurm.db import connect, init_db

    (tmp_path / "run.py").write_text("print('run')\n")
    script = tmp_path / "job.slurm"
    script.write_text("python3 run.py\n")
    job_dir = isolated_home / "jobs" / "123456"
    job_dir.mkdir(parents=True)
    (job_dir / "original.slurm").write_text(script.read_text())

    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into jobs (
              job_id, submitted_at, submit_cwd, original_script_path,
              copied_script_path, state, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "123456",
                "2026-05-22T00:00:00+00:00",
                str(tmp_path),
                str(script),
                str(job_dir / "instrumented.slurm"),
                "UNKNOWN",
                "t",
                "t",
            ),
        )
        conn.commit()

    assert static_analyze_submission("123456") == "created"
    assert static_analyze_submission("123456") == "created"

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        command_count = conn.execute("select count(*) from job_commands where job_id = '123456'").fetchone()[0]
        file_count = conn.execute(
            "select count(*) from job_files where job_id = '123456' and role = 'entry_file'"
        ).fetchone()[0]

    assert command_count == 1
    assert file_count == 1
