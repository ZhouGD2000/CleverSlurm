import json
import sqlite3
import urllib.error

from conftest import write_executable


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_feishu_signature_matches_lark_custom_bot_algorithm():
    from ai_slurm.notify.feishu import build_feishu_sign

    assert build_feishu_sign("test-secret", 1700000000) == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84="


def test_immediate_notification_is_sent_and_marked_sent(isolated_home, monkeypatch):
    from ai_slurm.db import connect, init_db
    from ai_slurm.notify.dispatcher import enqueue_job_notification
    from ai_slurm.notify.feishu import dispatch_pending

    monkeypatch.setenv("AI_SLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    analysis = {
        "job_id": "123456",
        "deterministic_status": "hard_failed",
        "semantic_status": "hard_failed",
        "failure_category": "FAILED",
        "severity": "high",
        "recommended_notification": "immediate",
        "title": "[AI-Slurm][FAILED] Job 123456",
        "body": "State: FAILED",
    }
    with connect() as conn:
        init_db(conn)
        notification_id = enqueue_job_notification(conn, analysis)
        conn.commit()

    dispatch_pending(urlopen=fake_urlopen)

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        row = conn.execute("select status, sent_at from notifications where id = ?", (notification_id,)).fetchone()

    assert len(sent) == 1
    assert sent[0]["msg_type"] == "interactive"
    assert row[0] == "sent"
    assert row[1] is not None


def test_duplicate_notifications_do_not_send_twice(isolated_home, monkeypatch):
    from ai_slurm.db import connect, init_db
    from ai_slurm.notify.dispatcher import enqueue_job_notification
    from ai_slurm.notify.feishu import dispatch_pending

    monkeypatch.setenv("AI_SLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    analysis = {
        "job_id": "123456",
        "deterministic_status": "hard_failed",
        "semantic_status": "hard_failed",
        "failure_category": "FAILED",
        "severity": "high",
        "recommended_notification": "immediate",
    }
    with connect() as conn:
        init_db(conn)
        first = enqueue_job_notification(conn, analysis)
        second = enqueue_job_notification(conn, analysis)
        conn.commit()

    dispatch_pending(urlopen=fake_urlopen)
    dispatch_pending(urlopen=fake_urlopen)

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        count = conn.execute("select count(*) from notifications").fetchone()[0]

    assert first == second
    assert count == 1
    assert len(sent) == 1


def test_feishu_dispatcher_retries_transient_http_failures():
    from ai_slurm.notify.feishu import send_payload

    calls = []

    def flaky_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.URLError("temporary")
        return FakeResponse({"code": 0, "msg": "ok"})

    send_payload(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test",
        {"msg_type": "text", "content": {"text": "AI-Slurm"}},
        urlopen=flaky_urlopen,
        backoff_seconds=0,
    )

    assert len(calls) == 2


def test_tracker_creates_and_dispatches_failed_notification(isolated_home, fake_bin, monkeypatch):
    from ai_slurm.db import connect, init_db
    from ai_slurm.slurm.tracker import track_once

    monkeypatch.setenv("AI_SLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    monkeypatch.setattr("ai_slurm.notify.feishu.DEFAULT_URLOPEN", fake_urlopen)
    write_executable(
        fake_bin / "sacct",
        "#!/bin/sh\nprintf 'JobID|State|ExitCode|DerivedExitCode|Elapsed|MaxRSS|NodeList\\n123456|FAILED|1:0|1:0|00:00:03|12M|node001\\n'\n",
    )

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, state, created_at, updated_at) "
            "values ('123456', 't', '/tmp', 'aisbatch job.slurm', 'UNKNOWN', 't', 't')"
        )

    track_once()

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        notification = conn.execute(
            "select mode, status, category from notifications where job_id = '123456'"
        ).fetchone()
        analysis = conn.execute(
            "select deterministic_status, failure_category from job_analysis where job_id = '123456'"
        ).fetchone()

    assert notification == ("immediate", "sent", "FAILED")
    assert analysis == ("hard_failed", "FAILED")
    assert len(sent) == 1
