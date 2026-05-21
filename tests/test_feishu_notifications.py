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
    from cslurm.notify.feishu import build_feishu_sign

    assert build_feishu_sign("test-secret", 1700000000) == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84="


def test_immediate_notification_is_sent_and_marked_sent(isolated_home, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.notify.dispatcher import enqueue_job_notification
    from cslurm.notify.feishu import dispatch_pending

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
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
        "title": "[CleverSlurm][FAILED] Job 123456",
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


def test_immediate_notification_burst_is_sent_as_one_group_summary(isolated_home, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.notify.dispatcher import enqueue_job_notification
    from cslurm.notify.feishu import dispatch_pending

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    monkeypatch.setenv("CSLURM_NOTIFICATION_IMMEDIATE_GROUP_THRESHOLD", "3")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    with connect() as conn:
        init_db(conn)
        for index in range(5):
            job_id = str(9000 + index)
            enqueue_job_notification(
                conn,
                {
                    "job_id": job_id,
                    "deterministic_status": "hard_failed",
                    "semantic_status": "hard_failed",
                    "failure_category": "FAILED",
                    "severity": "high",
                    "recommended_notification": "immediate",
                    "title": f"[CleverSlurm][FAILED] Job {job_id}",
                    "body": "State: FAILED",
                },
            )
        conn.commit()

    assert dispatch_pending(mode="immediate", urlopen=fake_urlopen) == 1

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        statuses = conn.execute("select distinct status from notifications").fetchall()
        batch = conn.execute("select mode, status, summary_json from notification_batches").fetchone()

    assert len(sent) == 1
    assert "[CleverSlurm][IMMEDIATE]" in sent[0]["card"]["header"]["title"]["content"]
    assert "5 job(s)" in sent[0]["card"]["header"]["title"]["content"]
    assert [row[0] for row in statuses] == ["batched"]
    assert batch[0] == "immediate"
    assert batch[1] == "sent"
    assert json.loads(batch[2])["total"] == 5


def test_immediate_notification_hundred_job_burst_is_one_group_summary(isolated_home, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.notify.dispatcher import enqueue_job_notification
    from cslurm.notify.feishu import dispatch_pending

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    with connect() as conn:
        init_db(conn)
        for index in range(100):
            job_id = str(9100 + index)
            enqueue_job_notification(
                conn,
                {
                    "job_id": job_id,
                    "deterministic_status": "hard_failed",
                    "semantic_status": "hard_failed",
                    "failure_category": "FAILED",
                    "severity": "high",
                    "recommended_notification": "immediate",
                    "title": f"[CleverSlurm][FAILED] Job {job_id}",
                    "body": "State: FAILED",
                },
            )
        conn.commit()

    assert dispatch_pending(mode="immediate", urlopen=fake_urlopen) == 1

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        pending_count = conn.execute("select count(*) from notifications where status = 'pending'").fetchone()[0]
        batch = conn.execute("select mode, status, summary_json from notification_batches").fetchone()

    assert len(sent) == 1
    assert "100 job(s)" in sent[0]["card"]["header"]["title"]["content"]
    assert pending_count == 0
    assert batch[0] == "immediate"
    assert batch[1] == "sent"
    assert json.loads(batch[2])["total"] == 100


def test_duplicate_notifications_do_not_send_twice(isolated_home, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.notify.dispatcher import enqueue_job_notification
    from cslurm.notify.feishu import dispatch_pending

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
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


def test_batch_notifications_send_one_group_summary(isolated_home, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.notify.dispatcher import enqueue_job_notification
    from cslurm.notify.feishu import dispatch_pending

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    with connect() as conn:
        init_db(conn)
        for job_id in ["101", "102", "103"]:
            conn.execute(
                "insert into jobs (job_id, state, exit_code, job_name, command, created_at, updated_at) "
                "values (?, 'COMPLETED', '0:0', ?, 'csbatch job.slurm', 't', 't')",
                (job_id, f"job-{job_id}"),
            )
            enqueue_job_notification(
                conn,
                {
                    "job_id": job_id,
                    "group_id": "scan-u-sweep",
                    "deterministic_status": "completed",
                    "semantic_status": "normal",
                    "failure_category": "NONE",
                    "severity": "normal",
                    "recommended_notification": "batch",
                    "title": f"[CleverSlurm][NONE] Job {job_id}",
                    "body": f"State: COMPLETED\nJob: {job_id}",
                },
            )
        conn.commit()

    assert dispatch_pending(mode="batch", force=True, urlopen=fake_urlopen) == 1

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        statuses = conn.execute("select status from notifications order by job_id").fetchall()
        batch = conn.execute(
            "select group_id, mode, status, summary_json from notification_batches"
        ).fetchone()

    assert len(sent) == 1
    assert sent[0]["msg_type"] == "interactive"
    assert "3 job(s)" in sent[0]["card"]["header"]["title"]["content"]
    assert [row[0] for row in statuses] == ["batched", "batched", "batched"]
    assert batch[0] == "scan-u-sweep"
    assert batch[1] == "batch"
    assert batch[2] == "sent"
    assert json.loads(batch[3])["total"] == 3


def test_digest_notifications_send_one_digest_summary(isolated_home, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.notify.dispatcher import enqueue_job_notification
    from cslurm.notify.feishu import dispatch_pending

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    with connect() as conn:
        init_db(conn)
        enqueue_job_notification(
            conn,
            {
                "job_id": "201",
                "deterministic_status": "cancelled_by_user",
                "semantic_status": "unknown",
                "failure_category": "USER_CANCELLED",
                "severity": "low",
                "recommended_notification": "digest",
                "title": "[CleverSlurm][USER_CANCELLED] Job 201",
                "body": "State: CANCELLED",
            },
        )
        conn.commit()

    assert dispatch_pending(mode="digest", force=True, urlopen=fake_urlopen) == 1

    with sqlite3.connect(isolated_home / "db.sqlite") as conn:
        notification_status = conn.execute("select status from notifications where job_id = '201'").fetchone()[0]
        batch = conn.execute("select mode, status from notification_batches").fetchone()

    assert notification_status == "batched"
    assert batch == ("digest", "sent")
    assert "[CleverSlurm][DIGEST]" in sent[0]["card"]["header"]["title"]["content"]


def test_cnotify_dispatch_accepts_batch_mode(monkeypatch, capsys):
    from cslurm.cli.cnotify import main

    calls = []

    def fake_dispatch_pending(*, limit, mode, force):
        calls.append((limit, mode, force))
        return 2

    monkeypatch.setattr("cslurm.cli.cnotify.dispatch_pending", fake_dispatch_pending)
    monkeypatch.setattr("sys.argv", ["cnotify", "dispatch", "--mode", "batch", "--force", "-n", "5"])

    main()

    assert calls == [(5, "batch", True)]
    assert capsys.readouterr().out == "sent 2 notification(s)\n"


def test_tracker_flushes_due_batch_notifications_without_new_jobs(isolated_home, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.slurm.tracker import track_once

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    monkeypatch.setenv("CSLURM_NOTIFICATION_BATCH_WINDOW_MINUTES", "30")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    monkeypatch.setattr("cslurm.notify.feishu.DEFAULT_URLOPEN", fake_urlopen)
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into notifications (
              job_id, group_id, created_at, severity, category, channel, mode,
              title, body, payload_json, status, dedupe_key
            ) values (
              '301', 'daily', '2000-01-01T00:00:00+00:00', 'normal', 'NONE', 'feishu', 'batch',
              '[CleverSlurm][NONE] Job 301', 'State: COMPLETED',
              '{"analysis":{"semantic_status":"normal"}}', 'pending', 'job:301:normal:NONE'
            )
            """
        )
        conn.commit()

    track_once()

    with connect() as conn:
        status = conn.execute("select status from notifications where job_id = '301'").fetchone()[0]

    assert status == "batched"
    assert len(sent) == 1


def test_tracker_flushes_due_batch_notifications_when_jobs_are_running(isolated_home, fake_bin, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.slurm.tracker import track_once

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    monkeypatch.setattr("cslurm.notify.feishu.DEFAULT_URLOPEN", fake_urlopen)
    write_executable(
        fake_bin / "sacct",
        "#!/bin/sh\nprintf 'JobID|State|ExitCode|DerivedExitCode|Elapsed|MaxRSS|NodeList\\n999|RUNNING|0:0|0:0|00:01:00||node001\\n'\n",
    )
    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, state, created_at, updated_at) values ('999', 'UNKNOWN', 't', 't')"
        )
        conn.execute(
            """
            insert into notifications (
              job_id, group_id, created_at, severity, category, channel, mode,
              title, body, payload_json, status, dedupe_key
            ) values (
              '302', 'daily', '2000-01-01T00:00:00+00:00', 'normal', 'NONE', 'feishu', 'batch',
              '[CleverSlurm][NONE] Job 302', 'State: COMPLETED',
              '{"analysis":{"semantic_status":"normal"}}', 'pending', 'job:302:normal:NONE'
            )
            """
        )
        conn.commit()

    track_once()

    with connect() as conn:
        status = conn.execute("select status from notifications where job_id = '302'").fetchone()[0]
        job_state = conn.execute("select state from jobs where job_id = '999'").fetchone()[0]

    assert status == "batched"
    assert job_state == "RUNNING"
    assert len(sent) == 1


def test_feishu_dispatcher_retries_transient_http_failures():
    from cslurm.notify.feishu import send_payload

    calls = []

    def flaky_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise urllib.error.URLError("temporary")
        return FakeResponse({"code": 0, "msg": "ok"})

    send_payload(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test",
        {"msg_type": "text", "content": {"text": "CleverSlurm"}},
        urlopen=flaky_urlopen,
        backoff_seconds=0,
    )

    assert len(calls) == 2


def test_tracker_creates_and_dispatches_failed_notification(isolated_home, fake_bin, monkeypatch):
    from cslurm.db import connect, init_db
    from cslurm.slurm.tracker import track_once

    monkeypatch.setenv("CSLURM_FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data.decode()))
        return FakeResponse({"code": 0, "msg": "ok"})

    monkeypatch.setattr("cslurm.notify.feishu.DEFAULT_URLOPEN", fake_urlopen)
    write_executable(
        fake_bin / "sacct",
        "#!/bin/sh\nprintf 'JobID|State|ExitCode|DerivedExitCode|Elapsed|MaxRSS|NodeList\\n123456|FAILED|1:0|1:0|00:00:03|12M|node001\\n'\n",
    )

    with connect() as conn:
        init_db(conn)
        conn.execute(
            "insert into jobs (job_id, submitted_at, submit_cwd, command, state, created_at, updated_at) "
            "values ('123456', 't', '/tmp', 'csbatch job.slurm', 'UNKNOWN', 't', 't')"
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
