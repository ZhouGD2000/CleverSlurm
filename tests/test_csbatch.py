import sqlite3
import subprocess
import sys

from conftest import write_executable


def _disable_auto_summary(monkeypatch):
    monkeypatch.setenv("CSLURM_AI_AUTO_SUMMARY", "false")


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


def test_csbatch_triggers_submission_summary_after_recording_job(isolated_home, fake_bin, tmp_path, monkeypatch):
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")
    calls = []

    def fake_auto_summarize(job_id):
        calls.append(job_id)
        with sqlite3.connect(isolated_home / "db.sqlite") as conn:
            conn.execute(
                "update jobs set summary_json = ? where job_id = ?",
                ('{"one_line_summary":"auto summary"}', job_id),
            )
            conn.execute(
                "insert into job_events (job_id, event_type, raw_output) values (?, ?, ?)",
                (job_id, "AI_SUMMARY_CREATED", '{"one_line_summary":"auto summary"}'),
            )
            conn.commit()
        return "created"

    monkeypatch.setattr("cslurm.cli.csbatch.auto_summarize_submission", fake_auto_summarize)

    from cslurm.cli.csbatch import submit_batch

    submit_batch([str(script)])

    assert calls == ["123456"]
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select summary_json from jobs where job_id = '123456'").fetchone()
        events = conn.execute(
            "select event_type from job_events where job_id = '123456' order by id"
        ).fetchall()

    assert row[0] == '{"one_line_summary":"auto summary"}'
    assert [event[0] for event in events] == ["SUBMITTED", "AI_SUMMARY_CREATED"]


def test_csbatch_records_summary_failure_without_breaking_submission(isolated_home, fake_bin, tmp_path, monkeypatch):
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    def fake_auto_summarize(job_id):
        from cslurm.ai.auto import record_auto_summary_failure
        record_auto_summary_failure(job_id, RuntimeError("temporary AI failure"))
        return "failed"

    monkeypatch.setattr("cslurm.cli.csbatch.auto_summarize_submission", fake_auto_summarize)

    from cslurm.cli.csbatch import submit_batch

    job_id = submit_batch([str(script)])

    assert job_id == "123456"
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        event = conn.execute(
            "select event_type, raw_output from job_events where job_id = '123456' and event_type = 'AI_SUMMARY_FAILED'"
        ).fetchone()

    assert event[0] == "AI_SUMMARY_FAILED"
    assert "temporary AI failure" in event[1]
