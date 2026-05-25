import sqlite3
import subprocess
import sys

from conftest import write_executable


def _disable_auto_summary(monkeypatch):
    monkeypatch.setenv("CSLURM_AI_AUTO_SUMMARY", "false")
    monkeypatch.setenv("CSLURM_STATIC_ANALYSIS", "false")


def test_csbatch_records_fake_sbatch_job_id_and_copies_scripts(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    write_executable(
        fake_bin / "sbatch",
        "#!/bin/sh\nprintf '123456\\n'\n",
    )
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\n#SBATCH --job-name=test-job\npython run.py\n")

    from cslurm.cli.csbatch import submit_batch

    job_id = submit_batch([str(script)])

    assert job_id == "123456"
    job_dir = isolated_home / "jobs" / "123456"
    assert (job_dir / "original.slurm").read_text() == script.read_text()
    instrumented = (job_dir / "instrumented.slurm").read_text()
    assert "CSLURM_LOG_DIR" in instrumented
    assert "python run.py" in instrumented
    assert instrumented.index("#SBATCH --job-name=test-job") < instrumented.index("CSLURM_LOG_DIR")

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute(
            "select job_id, command, original_script_path, job_name, state from jobs"
        ).fetchone()
        event = conn.execute(
            "select event_type, raw_output from job_events where job_id = ?",
            ("123456",),
        ).fetchone()

    assert row[0] == "123456"
    assert "job.slurm" in row[1]
    assert row[2] == str(script)
    assert row[3] == "test-job"
    assert row[4] == "UNKNOWN"
    assert event == ("SUBMITTED", "123456\n")


def test_csbatch_records_stdout_and_stderr_paths_with_job_id(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text(
        "#!/bin/bash\n"
        "#SBATCH --output=logs/smoke-%j.out\n"
        "#SBATCH --error logs/smoke-%j.err\n"
        "hostname\n"
    )

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select stdout_path, stderr_path from jobs where job_id = '123456'").fetchone()

    assert row == (str(tmp_path / "logs" / "smoke-123456.out"), str(tmp_path / "logs" / "smoke-123456.err"))


def test_csbatch_records_squeue_like_submission_metadata(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    monkeypatch.setenv("USER", "zgd")
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text(
        "#!/bin/bash\n"
        "#SBATCH --job-name=from-script\n"
        "#SBATCH --partition=CPU1\n"
        "#SBATCH --nodes=1\n"
        "#SBATCH --nodelist=node001\n"
        "#SBATCH --account=oldacct\n"
        "#SBATCH --qos=normal\n"
        "#SBATCH --array=1-3\n"
        "#SBATCH --dependency=afterok:111\n"
        "hostname\n"
    )

    from cslurm.cli.csbatch import submit_batch

    submit_batch(["--job-name", "from-cli", "-p", "CPU2", "-N2", str(script)])

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute(
            """
            select user, job_name, partition, account, qos, array_spec,
                   dependency, nodes, nodelist
            from jobs where job_id = '123456'
            """
        ).fetchone()

    assert row == (
        "zgd",
        "from-cli",
        "CPU2",
        "oldacct",
        "normal",
        "1-3",
        "afterok:111",
        "2",
        "node001",
    )


def test_csbatch_records_partition_and_name_from_script_directives(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text(
        "#!/bin/bash\n"
        "#SBATCH --job-name=from-script\n"
        "#SBATCH -p CPU2\n"
        "hostname\n"
    )

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select job_name, partition from jobs where job_id = '123456'").fetchone()

    assert row == ("from-script", "CPU2")


def test_csbatch_records_default_slurm_log_path_when_not_configured(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select stdout_path, stderr_path from jobs where job_id = '123456'").fetchone()

    default_log = str(tmp_path / "slurm-123456.out")
    assert row == (default_log, default_log)


def test_csbatch_records_copied_scripts_as_job_files(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        rows = conn.execute(
            "select relpath, role, source, copied from job_files where job_id = '123456' order by relpath"
        ).fetchall()

    assert rows == [
        ("instrumented.slurm", "instrumented_script", "csbatch", 1),
        ("original.slurm", "original_script", "csbatch", 1),
    ]


def test_csbatch_passes_sbatch_options_and_script_args_to_real_sbatch(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    calls = tmp_path / "sbatch.calls"
    write_executable(
        fake_bin / "sbatch",
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {calls}\nprintf '123456\\n'\n",
    )
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    from cslurm.cli.csbatch import submit_batch

    submit_batch(["-p", "CPU2", "--time=00:01:00", str(script), "arg1", "arg2"])

    called_args = calls.read_text().splitlines()
    assert called_args[:4] == ["--parsable", "-p", "CPU2", "--time=00:01:00"]
    assert called_args[4].endswith("job.instrumented.slurm")
    assert called_args[5:] == ["arg1", "arg2"]


def test_csbatch_does_not_treat_chdir_argument_as_script(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    calls = tmp_path / "sbatch.calls"
    chdir = tmp_path / "work"
    chdir.mkdir()
    write_executable(
        fake_bin / "sbatch",
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {calls}\nprintf '123456\\n'\n",
    )
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    from cslurm.cli.csbatch import submit_batch

    submit_batch(["--chdir", str(chdir), str(script)])

    called_args = calls.read_text().splitlines()
    assert called_args[:3] == ["--parsable", "--chdir", str(chdir)]
    assert called_args[3].endswith("job.instrumented.slurm")


def test_csbatch_wrap_is_translated_to_instrumented_script(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    calls = tmp_path / "sbatch.calls"
    write_executable(
        fake_bin / "sbatch",
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {calls}\nprintf '123456\\n'\n",
    )

    from cslurm.cli.csbatch import submit_batch

    submit_batch(["-p", "CPU2", "--wrap", "echo wrapped"])

    called_args = calls.read_text().splitlines()
    assert called_args[:3] == ["--parsable", "-p", "CPU2"]
    assert "--wrap" not in called_args
    instrumented = (isolated_home / "jobs" / "123456" / "instrumented.slurm").read_text()
    assert "echo wrapped" in instrumented
    assert "PROGRAM_FINISHED" in instrumented


def test_csbatch_instrumented_script_records_program_finish(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\n#SBATCH --job-name=test-job\npython run.py\n")

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    instrumented = (isolated_home / "jobs" / "123456" / "instrumented.slurm").read_text()
    assert "CSLURM_ROOT=" in instrumented
    assert "finish.log" in instrumented
    assert "PROGRAM_FINISHED" in instrumented
    assert "$CSLURM_ROOT/wrappers" not in instrumented


def test_csbatch_module_entrypoint_runs_main(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    result = subprocess.run(
        [sys.executable, "-m", "cslurm.cli.csbatch", str(script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert result.stdout == "Submitted batch job 123456\n"


def test_csbatch_module_entrypoint_preserves_parsable_output(isolated_home, fake_bin, tmp_path, monkeypatch):
    _disable_auto_summary(monkeypatch)
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    result = subprocess.run(
        [sys.executable, "-m", "cslurm.cli.csbatch", "--parsable", str(script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert result.stdout == "123456\n"


def test_csbatch_queues_submission_summary_after_recording_job(isolated_home, fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("CSLURM_STATIC_ANALYSIS", "false")
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")
    launched = []

    def fake_launch(job_id):
        from cslurm.ai.auto import record_auto_summary_queued
        launched.append(job_id)
        record_auto_summary_queued(job_id, pid=777)
        return "queued"

    monkeypatch.setattr("cslurm.cli.csbatch.launch_auto_summary", fake_launch)

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    assert launched == ["123456"]
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select summary_json from jobs where job_id = '123456'").fetchone()
        events = conn.execute(
            "select event_type, raw_output from job_events where job_id = '123456' order by id"
        ).fetchall()

    assert row[0] is None
    assert [event[0] for event in events] == ["SUBMITTED", "AI_SUMMARY_QUEUED"]
    assert events[1][1] == "pid=777"


def test_csbatch_records_summary_queue_failure_without_breaking_submission(isolated_home, fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("CSLURM_STATIC_ANALYSIS", "false")
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    def fake_launch(job_id):
        from cslurm.ai.auto import record_auto_summary_failure
        record_auto_summary_failure(job_id, RuntimeError("could not start AI worker"))
        return "failed"

    monkeypatch.setattr("cslurm.cli.csbatch.launch_auto_summary", fake_launch)

    from cslurm.cli.csbatch import submit_batch

    job_id = submit_batch([str(script)])

    assert job_id == "123456"
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        event = conn.execute(
            "select event_type, raw_output from job_events where job_id = '123456' and event_type = 'AI_SUMMARY_FAILED'"
        ).fetchone()

    assert event[0] == "AI_SUMMARY_FAILED"
    assert "could not start AI worker" in event[1]


def test_launch_auto_summary_starts_detached_worker(isolated_home, monkeypatch):
    from cslurm.cli.csbatch import launch_auto_summary

    launched = []

    class FakeProcess:
        pid = 888

    def fake_popen(argv, **kwargs):
        launched.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr("cslurm.cli.csbatch.subprocess.Popen", fake_popen)

    assert launch_auto_summary("123456") == "queued"

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        event = conn.execute(
            "select event_type, raw_output from job_events where job_id = '123456'"
        ).fetchone()

    assert launched[0][0][-3:] == ["-m", "cslurm.ai.auto", "123456"]
    assert launched[0][1]["start_new_session"] is True
    assert event == ("AI_SUMMARY_QUEUED", "pid=888")


def test_csbatch_queues_static_analysis_after_recording_job(isolated_home, fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("CSLURM_AI_AUTO_SUMMARY", "false")
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\npython run.py\n")
    launched = []

    def fake_launch(job_id):
        from cslurm.collect.static_auto import record_static_analysis_queued

        launched.append(job_id)
        record_static_analysis_queued(job_id, pid=999)
        return "queued"

    monkeypatch.setattr("cslurm.cli.csbatch.launch_static_analysis", fake_launch)

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    assert launched == ["123456"]
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        events = conn.execute(
            "select event_type, raw_output from job_events where job_id = '123456' order by id"
        ).fetchall()

    assert events == [("SUBMITTED", "123456\n"), ("STATIC_ANALYSIS_QUEUED", "pid=999")]


def test_launch_static_analysis_starts_detached_worker(isolated_home, monkeypatch):
    from cslurm.cli.csbatch import launch_static_analysis

    launched = []

    class FakeProcess:
        pid = 889

    def fake_popen(argv, **kwargs):
        launched.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr("cslurm.cli.csbatch.subprocess.Popen", fake_popen)

    assert launch_static_analysis("123456") == "queued"

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        event = conn.execute(
            "select event_type, raw_output from job_events where job_id = '123456'"
        ).fetchone()

    assert launched[0][0][-3:] == ["-m", "cslurm.collect.static_auto", "123456"]
    assert launched[0][1]["start_new_session"] is True
    assert event == ("STATIC_ANALYSIS_QUEUED", "pid=889")


def test_background_auto_summary_worker_records_created_summary(isolated_home, monkeypatch):
    from cslurm.ai.auto import main
    from cslurm.db import connect, init_db

    def fake_summarize_submission(job_id):
        with connect() as conn:
            conn.execute(
                "update jobs set summary_json = ? where job_id = ?",
                ('{"one_line_summary":"auto summary"}', job_id),
            )
            conn.execute(
                "insert into job_events (job_id, event_type, raw_output) values (?, ?, ?)",
                (job_id, "AI_SUMMARY_CREATED", '{"one_line_summary":"auto summary"}'),
            )
            conn.commit()

    monkeypatch.setattr("cslurm.ai.auto.summarize_submission", fake_summarize_submission)
    monkeypatch.setattr("sys.argv", ["auto", "123456"])
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, state, created_at, updated_at) values ('123456', 'UNKNOWN', 't', 't')"
        )
        conn.commit()

    main()

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select summary_json from jobs where job_id = '123456'").fetchone()
        event = conn.execute(
            "select event_type from job_events where job_id = '123456' and event_type = 'AI_SUMMARY_CREATED'"
        ).fetchone()

    assert row[0] == '{"one_line_summary":"auto summary"}'
    assert event[0] == "AI_SUMMARY_CREATED"
