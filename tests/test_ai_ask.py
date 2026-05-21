import json

from cslurm.ai.ask import answer_question
from cslurm.db import connect, init_db


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.messages = None

    def chat_json(self, messages):
        raise AssertionError("cjobs ask should not make a JSON request")

    def chat_raw(self, messages):
        self.messages = messages
        return self.content


class FailingClient:
    def chat_json(self, messages):
        raise AssertionError("cjobs ask should not make a JSON request")

    def chat_raw(self, messages):
        raise RuntimeError("AI model request timed out")


def test_answer_question_sends_recent_job_facts_to_ai(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, job_name, state, exit_code, summary_json, completion_summary_json, created_at, updated_at) "
            "values ('46644', '2026-05-16T18:45:57+00:00', '/work', 'csbatch smoke_real.slurm', 'cleverslurm-smoke', 'COMPLETED', '0:0', ?, ?, 't', 't')",
            (
                json.dumps({"one_line_summary": "Run a smoke test."}),
                json.dumps({"human_summary": "The smoke job completed successfully."}),
            ),
        )
        conn.execute(
            "insert into job_events (job_id, event_time, event_type, raw_output) "
            "values ('46644', '2026-05-16T18:46:11+00:00', 'STATE_CHANGED', 'UNKNOWN -> COMPLETED')"
        )
        conn.execute(
            "insert into job_commands (job_id, time, hostname, cwd, kind, executable, argv, entry_file, source) "
            "values ('46644', '2026-05-16T18:45:59+00:00', 'cpu12', '/work', 'python', '/usr/bin/python3', '[\"-c\", \"print(1)\"]', null, 'runtime-wrapper')"
        )

    fake = FakeClient("最近完成了 46644：一次 CleverSlurm smoke test。")

    answer = answer_question("最近完成了什么任务？都是些什么工作？", client=fake, limit=5)

    assert answer == "最近完成了 46644：一次 CleverSlurm smoke test。"
    assert "最近完成了什么任务" in fake.messages[1]["content"]
    assert "46644" in fake.messages[1]["content"]
    assert "Run a smoke test." in fake.messages[1]["content"]
    assert "UNKNOWN -> COMPLETED" in fake.messages[1]["content"]


def test_answer_question_returns_local_message_for_empty_ai_answer(isolated_home):
    fake = FakeClient("")

    answer = answer_question("最近完成了什么任务？", client=fake)

    assert "没有找到最近任务记录" in answer


def test_answer_question_does_not_send_ai_failure_events(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, job_name, state, created_at, updated_at) "
            "values ('46644', '2026-05-16T18:45:57+00:00', '/work', 'csbatch smoke.slurm', 'smoke', 'COMPLETED', 't', 't')"
        )
        conn.execute(
            "insert into job_events (job_id, event_time, event_type, raw_output) "
            "values ('46644', 't', 'AI_LOG_ANALYSIS_FAILED', 'RuntimeError: model timeout')"
        )
        conn.execute(
            "insert into job_events (job_id, event_time, event_type, raw_output) "
            "values ('46644', 't', 'STATE_CHANGED', 'UNKNOWN -> COMPLETED')"
        )

    fake = FakeClient("ok")

    answer_question("最近完成了什么任务？", client=fake)

    content = fake.messages[1]["content"]
    assert "AI_LOG_ANALYSIS_FAILED" not in content
    assert "model timeout" not in content
    assert "STATE_CHANGED" in content


def test_answer_question_returns_deterministic_fallback_when_ai_fails(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, job_name, state, created_at, updated_at) "
            "values ('46644', '2026-05-16T18:45:57+00:00', '/work', 'csbatch smoke.slurm', 'smoke', 'COMPLETED', 't', 't')"
        )

    answer = answer_question("最近运行了什么任务？", client=FailingClient())

    assert "AI 请求失败" in answer
    assert "46644" in answer
    assert "COMPLETED" in answer
    assert "csbatch smoke.slurm" in answer
