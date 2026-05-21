import json
import sqlite3

import pytest


class FakeSemanticClient:
    def __init__(self, content: str):
        self.content = content

    def chat_json(self, messages):
        self.messages = messages
        return self.content


def test_failed_state_creates_immediate_high_severity_analysis(isolated_home):
    from ai_slurm.db import connect, init_db
    from ai_slurm.notify.analysis import analyze_job

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, state, exit_code, derived_exit_code, created_at, updated_at) "
            "values ('123456', 'OUT_OF_MEMORY', '0:9', '0:9', 't', 't')"
        )
        analysis = analyze_job(conn, "123456")

    assert analysis["hard_failed"] is True
    assert analysis["deterministic_status"] == "hard_failed"
    assert analysis["failure_category"] == "OUT_OF_MEMORY"
    assert analysis["severity"] == "high"
    assert analysis["recommended_notification"] == "immediate"


def test_nonzero_derived_exit_code_creates_hard_failure(isolated_home):
    from ai_slurm.db import connect, init_db
    from ai_slurm.notify.analysis import analyze_job

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, state, exit_code, derived_exit_code, created_at, updated_at) "
            "values ('123456', 'COMPLETED', '0:0', '1:0', 't', 't')"
        )
        analysis = analyze_job(conn, "123456")

    assert analysis["hard_failed"] is True
    assert analysis["failure_category"] == "NONZERO_DERIVED_EXITCODE"
    assert analysis["recommended_notification"] == "immediate"


def test_cancelled_with_user_cancel_event_is_digest(isolated_home):
    from ai_slurm.db import connect, init_db
    from ai_slurm.notify.analysis import analyze_job

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, state, exit_code, created_at, updated_at) "
            "values ('123456', 'CANCELLED', '0:0', 't', 't')"
        )
        conn.execute(
            "insert into job_events (job_id, event_type, note) values ('123456', 'CANCEL_REQUESTED', 'wrong U')"
        )
        analysis = analyze_job(conn, "123456")

    assert analysis["deterministic_status"] == "cancelled_by_user"
    assert analysis["failure_category"] == "USER_CANCELLED"
    assert analysis["severity"] == "low"
    assert analysis["recommended_notification"] == "digest"


def test_completed_log_converged_false_becomes_semantic_failed(isolated_home, tmp_path):
    from ai_slurm.db import connect, init_db
    from ai_slurm.notify.analysis import analyze_job, record_job_analysis

    stdout = tmp_path / "slurm-123456.out"
    stdout.write_text("iter 199: diff = 1.1e-3\nMax iteration reached\nconverged = false\n")

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, state, exit_code, stdout_path, created_at, updated_at) "
            "values ('123456', 'COMPLETED', '0:0', ?, 't', 't')",
            (str(stdout),),
        )
        analysis = analyze_job(conn, "123456")
        record_job_analysis(conn, analysis)
        row = conn.execute("select semantic_status, failure_category, evidence_json from job_analysis").fetchone()

    assert analysis["hard_failed"] is False
    assert analysis["semantic_status"] == "semantic_failed"
    assert analysis["failure_category"] == "NOT_CONVERGED"
    assert analysis["recommended_notification"] == "immediate"
    assert row[0] == "semantic_failed"
    assert row[1] == "NOT_CONVERGED"
    assert "converged = false" in json.loads(row[2])["matched_windows"][0]["lines"][-1]


def test_completed_job_missing_required_output_file_fails_success_criteria(isolated_home, tmp_path, monkeypatch):
    from ai_slurm.db import connect, init_db
    from ai_slurm.notify.analysis import analyze_job

    criteria = tmp_path / "criteria.yml"
    criteria.write_text(
        "success_criteria:\n"
        "  require_output_files:\n"
        "    - results/*.jld2\n"
    )
    monkeypatch.setenv("AI_SLURM_SUCCESS_CRITERIA", str(criteria))

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, state, exit_code, submit_cwd, created_at, updated_at) "
            "values ('123456', 'COMPLETED', '0:0', ?, 't', 't')",
            (str(tmp_path),),
        )
        analysis = analyze_job(conn, "123456")

    assert analysis["semantic_status"] == "semantic_failed"
    assert analysis["failure_category"] == "SUCCESS_CRITERIA_NOT_MET"
    assert analysis["evidence"]["success_criteria_failures"][0]["pattern"] == "results/*.jld2"


def test_init_db_migrates_existing_jobs_table_with_notification_columns(tmp_path, monkeypatch):
    from ai_slurm.db import connect, init_db

    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setenv("AI_SLURM_ROOT", str(root))
    db = root / "db.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("create table jobs (job_id text primary key, state text)")

    with connect() as conn:
        init_db(conn)
        columns = {row["name"] for row in conn.execute("pragma table_info(jobs)").fetchall()}
        tables = {row["name"] for row in conn.execute("select name from sqlite_master where type = 'table'")}

    assert "derived_exit_code" in columns
    assert "notifications" in tables
    assert "job_analysis" in tables


def test_ai_semantic_parser_rejects_malformed_json():
    from ai_slurm.notify.semantic import parse_ai_analysis_json

    with pytest.raises(ValueError, match="valid JSON"):
        parse_ai_analysis_json("{not json")


def test_ai_semantic_parser_accepts_fenced_json():
    from ai_slurm.notify.semantic import parse_ai_analysis_json

    parsed = parse_ai_analysis_json(
        "```json\n"
        "{"
        "\"semantic_status\":\"normal\","
        "\"failure_category\":\"NONE\","
        "\"confidence\":0.8,"
        "\"short_summary\":\"ok\","
        "\"evidence\":[],"
        "\"resource_notes\":[],"
        "\"recommended_notification\":\"batch\","
        "\"suggested_next_steps\":[]"
        "}\n"
        "```"
    )

    assert parsed["semantic_status"] == "normal"
    assert parsed["failure_category"] == "NONE"


def test_ai_semantic_analysis_does_not_override_hard_failure(isolated_home, monkeypatch):
    from ai_slurm.db import connect, init_db
    from ai_slurm.notify.analysis import analyze_job
    from ai_slurm.notify.semantic import merge_ai_analysis, request_ai_semantic_analysis

    monkeypatch.setenv("AI_SLURM_NOTIFICATION_AI_ANALYSIS", "true")
    client = FakeSemanticClient(
        json.dumps(
            {
                "semantic_status": "normal",
                "failure_category": "NONE",
                "confidence": 1.0,
                "short_summary": "ignore factual failure",
                "evidence": [],
                "resource_notes": [],
                "recommended_notification": "batch",
                "suggested_next_steps": [],
            }
        )
    )

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, state, exit_code, created_at, updated_at) "
            "values ('123456', 'FAILED', '1:0', 't', 't')"
        )
        analysis = analyze_job(conn, "123456")
        ai_analysis = request_ai_semantic_analysis(conn, "123456", analysis, client=client)
        merged = merge_ai_analysis(analysis, ai_analysis)

    assert merged["deterministic_status"] == "hard_failed"
    assert merged["semantic_status"] == "hard_failed"
    assert merged["failure_category"] == "FAILED"
    assert "untrusted program output" in client.messages[0]["content"]
