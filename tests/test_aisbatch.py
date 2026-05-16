import sqlite3
import subprocess
import sys

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
    assert instrumented.index("#SBATCH --job-name=test-job") < instrumented.index("AI_SLURM_LOG_DIR")

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


def test_aisbatch_records_stdout_and_stderr_paths_with_job_id(isolated_home, fake_bin, tmp_path):
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text(
        "#!/bin/bash\n"
        "#SBATCH --output=logs/smoke-%j.out\n"
        "#SBATCH --error logs/smoke-%j.err\n"
        "hostname\n"
    )

    from ai_slurm.cli.aisbatch import submit_batch

    submit_batch([str(script)])

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select stdout_path, stderr_path from jobs where job_id = '123456'").fetchone()

    assert row == (str(tmp_path / "logs" / "smoke-123456.out"), str(tmp_path / "logs" / "smoke-123456.err"))


def test_aisbatch_module_entrypoint_runs_main(isolated_home, fake_bin, tmp_path):
    write_executable(fake_bin / "sbatch", "#!/bin/sh\nprintf '123456\\n'\n")
    script = tmp_path / "job.slurm"
    script.write_text("#!/bin/bash\nhostname\n")

    result = subprocess.run(
        [sys.executable, "-m", "ai_slurm.cli.aisbatch", str(script)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert result.stdout == "123456\n"
