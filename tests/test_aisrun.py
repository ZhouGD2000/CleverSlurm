import sqlite3

from conftest import write_executable


def test_aisrun_records_standalone_fake_srun_execution(isolated_home, fake_bin, tmp_path, monkeypatch):
    write_executable(
        fake_bin / "srun",
        "#!/bin/sh\nprintf 'ran %s %s\\n' \"$1\" \"$2\"\n",
    )
    monkeypatch.chdir(tmp_path)

    from ai_slurm.cli.aisrun import run_job

    result = run_job(["python", "script.py"])

    assert result.stdout == "ran python script.py\n"
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        job = conn.execute(
            "select job_id, command, state, submit_cwd from jobs"
        ).fetchone()
        event = conn.execute(
            "select event_type, command, raw_output from job_events where job_id = ?",
            (job[0],),
        ).fetchone()

    assert job[0].startswith("local-")
    assert job[1] == "aisrun python script.py"
    assert job[2] == "COMPLETED"
    assert job[3] == str(tmp_path)
    assert event[0] == "COMPLETED"
    assert event[1] == "srun python script.py"
    assert event[2].startswith("ran python script.py\n")
