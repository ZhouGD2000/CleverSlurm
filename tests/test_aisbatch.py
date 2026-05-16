import sqlite3

from conftest import write_executable


def test_aisbatch_records_fake_sbatch_job_id_and_copies_scripts(isolated_home, fake_bin, tmp_path):
    write_executable(
        fake_bin / "sbatch",
        "#!/bin/sh\nprintf '123456\\n'\n",
    )
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\n#SBATCH --job-name=test-job\npython run.py\n")

    from ai_slurm.cli.aisbatch import submit_batch

    job_id = submit_batch([str(script)])

    assert job_id == "123456"
    job_dir = isolated_home / "jobs" / "123456"
    assert (job_dir / "original.slurm").read_text() == script.read_text()
    instrumented = (job_dir / "instrumented.slurm").read_text()
    assert "AI_SLURM_LOG_DIR" in instrumented
    assert "python run.py" in instrumented

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
