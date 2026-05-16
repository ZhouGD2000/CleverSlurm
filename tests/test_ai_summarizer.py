import json
import sqlite3

from ai_slurm.ai.summarize import summarize_completion, summarize_submission
from ai_slurm.db import connect, init_db


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.messages = None

    def chat_json(self, messages):
        self.messages = messages
        return json.dumps(self.content)


def test_summarize_submission_writes_structured_json_and_event(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, job_name, state, created_at, updated_at) "
            "values ('123456', 't', '/work', 'aisbatch job.slurm', 'dmft', 'UNKNOWN', 't', 't')"
        )
        conn.execute(
            "insert into job_files (job_id, path, relpath, size, role, source, copied, confidence) "
            "values ('123456', '/work/run.jl', 'run.jl', 12, 'entry_file', 'runtime-command', 1, 1.0)"
        )

    fake = FakeClient(
        {
            "job_id": "123456",
            "one_line_summary": "Run DMFT test.",
            "tags": ["Julia", "DMFT"],
            "summary_confidence": 0.8,
        }
    )

    summary = summarize_submission("123456", client=fake)

    assert summary["one_line_summary"] == "Run DMFT test."
    assert "aisbatch job.slurm" in fake.messages[1]["content"]
    assert "run.jl" in fake.messages[1]["content"]

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select summary_json from jobs where job_id = '123456'").fetchone()
        event = conn.execute("select event_type from job_events where job_id = '123456'").fetchone()

    assert json.loads(row[0])["tags"] == ["Julia", "DMFT"]
    assert event[0] == "AI_SUMMARY_CREATED"


def test_summarize_completion_writes_completion_summary(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, state, exit_code, elapsed, max_rss, created_at, updated_at) "
            "values ('123456', 't', '/work', 'aisbatch job.slurm', 'FAILED', '1:0', '00:00:10', '2G', 't', 't')"
        )

    fake = FakeClient(
        {
            "job_id": "123456",
            "completion_status": "FAILED",
            "failure_category": "RUNTIME_ERROR",
            "confidence": 0.7,
        }
    )

    summary = summarize_completion("123456", client=fake)

    assert summary["completion_status"] == "FAILED"
    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select completion_summary_json from jobs where job_id = '123456'").fetchone()
        event = conn.execute("select event_type from job_events where job_id = '123456'").fetchone()

    assert json.loads(row[0])["failure_category"] == "RUNTIME_ERROR"
    assert event[0] == "AI_COMPLETION_SUMMARY_CREATED"
