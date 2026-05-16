from ai_slurm.db import connect, init_db
from ai_slurm.cli.aijobs import show_job


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
