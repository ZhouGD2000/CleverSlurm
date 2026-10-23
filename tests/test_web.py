import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing

import pytest

from cslurm.db import connect, init_db
from cslurm.web import data
from cslurm.web.operations import Operations
from cslurm.web.server import ConsoleServer
from conftest import write_executable


@pytest.fixture
def web_db(isolated_home):
    with closing(connect()) as conn:
        init_db(conn)
        conn.execute("insert into jobs (job_id,job_name,partition,state,submitted_at,summary_json) values ('100','<script>alert(1)</script>','cpu','RUNNING','2026-01-01',?)", (json.dumps({"one_line_summary": "Saved summary"}),))
        conn.execute("insert into jobs (job_id,job_name,partition,state,submitted_at) values ('101','second','gpu','COMPLETED','2026-01-02')")
        conn.execute("insert into jobs (job_id,job_name,partition,state,submitted_at,array_spec) values ('102','array','cpu','RUNNING','2026-01-03','1-10')")
        conn.commit()
    return isolated_home


@pytest.fixture
def server(web_db):
    instance = ConsoleServer(0)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=3)


def request(server, path, *, body=None, auth=True, headers=None):
    url = f"http://127.0.0.1:{server.server_port}{path}"
    request_headers = {"Authorization": "Bearer " + server.token} if auth else {}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    request_headers.update(headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=request_headers)
    try:
        response = urllib.request.urlopen(req, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        content = response.read()
        return response.status, json.loads(content) if response.headers.get_content_type() == "application/json" else content


def wait_operation(operations, op):
    until = time.monotonic() + 8
    while time.monotonic() < until:
        result = operations.get(op["id"])
        if result["status"] != "running":
            return result
        time.sleep(0.02)
    pytest.fail("operation did not finish")


def test_queries_are_paginated_filtered_and_read_only(web_db):
    before = (web_db / "db.sqlite").stat().st_mtime_ns
    assert data.overview()["total"] == 3
    result = data.list_jobs(limit=1)
    assert result["total"] == 3
    assert result["items"][0]["job_id"] == "102"
    assert data.list_jobs(partition="gpu")["items"][0]["job_id"] == "101"
    assert data.list_jobs(state="active")["total"] == 2
    assert data.list_jobs(search="%' OR 1=1 --")["total"] == 0
    assert data.detail("100")["job"]["summary"]["one_line_summary"] == "Saved summary"
    assert (web_db / "db.sqlite").stat().st_mtime_ns == before


def test_server_assets_auth_validation_and_unknown_routes(server):
    assert request(server, "/", auth=False)[0] == 200
    assert request(server, "/icons/cpu.svg", auth=False)[0] == 200
    assert request(server, "/api/overview", auth=False)[0] == 403
    assert request(server, "/api/overview")[1]["total"] == 3
    assert request(server, "/api/jobs/100")[1]["job"]["job_name"].startswith("<script>")
    assert request(server, "/api/jobs?limit=100000")[0] == 400
    assert request(server, "/api/jobs?offset=-1")[0] == 400
    assert request(server, "/api/jobs/does-not-exist")[0] == 404
    assert request(server, "/api/jobs/100/logs?lines=2000")[0] == 400
    assert request(server, "/api/other")[0] == 404
    assert request(server, "/../server.py", auth=False)[0] == 404
    assert request(server, "/api/overview", headers={"Host": "attacker.example"})[0] == 403
    assert request(server, "/api/operations", body={"action":"refresh"}, headers={"Origin":"https://attacker.example"})[0] == 403
    assert request(server, "/api/operations", body={"action":"cancel","job_id":"100","confirm_job_id":"100"})[0] == 403
    assert request(server, "/api/operations", body={"action":"shell","command":"touch /tmp/no"})[0] == 400
    assert request(server, "/api/operations", body=[])[0] == 400


def test_read_only_and_demo_reject_operations(web_db):
    for operations in [Operations(read_only=True, allow_cancel=True), Operations(demo=True, allow_cancel=True)]:
        for action in ["refresh", "ask", "summarize", "cancel"]:
            with pytest.raises(PermissionError):
                operations.start(action, {"job_id":"100", "confirm_job_id":"100", "question":"hello"})


def test_cancel_rejects_bulk_terminal_unknown_and_wrong_confirmation(web_db):
    operations = Operations(allow_cancel=True)
    for job_id, confirm in [("--all", "--all"), ("100,101", "100,101"), ("100", "101"), ("101", "101"), ("102", "102")]:
        with pytest.raises(ValueError):
            operations.start("cancel", {"job_id":job_id, "confirm_job_id":confirm})
    with pytest.raises(KeyError):
        operations.start("cancel", {"job_id":"999", "confirm_job_id":"999"})


def test_confirmed_cancel_only_passes_single_job_to_fake_slurm(web_db, fake_bin, tmp_path):
    capture = tmp_path / "cancel-args"
    write_executable(fake_bin / "scancel", f'#!/bin/sh\nprintf "%s\\n" "$@" > "{capture}"\n')
    operations = Operations(allow_cancel=True)
    op = operations.start("cancel", {"job_id":"100", "confirm_job_id":"100"})
    result = wait_operation(operations, op)
    assert result["status"] == "succeeded", result
    assert capture.read_text().splitlines() == ["100"]
    with data.reader() as conn:
        assert conn.execute("select event_type from job_events where job_id='100'").fetchone()[0] == "CANCEL_REQUESTED"


def test_refresh_uses_fake_sacct_and_is_nonblocking(web_db, fake_bin, monkeypatch):
    monkeypatch.setenv("CSLURM_NOTIFICATION_ENABLED", "false")
    monkeypatch.setenv("CSLURM_NOTIFICATION_AI_ANALYSIS", "false")
    write_executable(fake_bin / "sacct", "#!/bin/sh\nsleep 0.2\nprintf '100|RUNNING|0:0|0:0|00:02:03|12M|node1\\n102|RUNNING|0:0|0:0|00:02:03|12M|node1\\n'\n")
    operations = Operations()
    op = operations.start("refresh", {})
    assert op["status"] == "running"
    from cslurm.web.operations import BusyError
    with pytest.raises(BusyError):
        operations.start("refresh", {})
    result = wait_operation(operations, op)
    assert result["status"] == "succeeded", result
    assert data.get_job("100")["elapsed"] == "00:02:03"


def test_refresh_respects_cron_lock(web_db):
    import fcntl
    with (web_db / "ctrack.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        operations = Operations()
        result = wait_operation(operations, operations.start("refresh", {}))
    assert result["status"] == "failed"
    assert "already running" in result["error"]


def test_bounded_log_read_and_missing_file(web_db):
    log = web_db / "large.out"
    log.write_text("entry\n" * 100000 + "final\n")
    result = data.tail(log, 5)
    assert result["text"].endswith("final")
    assert len(result["text"].splitlines()) == 5
    assert result["truncated"]
    assert data.tail(web_db / "missing", 5)["error"]
    fifo = web_db / "pipe"
    os.mkfifo(fifo)
    assert "regular file" in data.tail(fifo, 5)["error"]
    with sqlite3.connect(web_db / "db.sqlite") as conn:
        conn.execute("update jobs set stdout_path=?, submit_cwd=? where job_id='100'", ("large.out", str(web_db)))
    assert data.logs("100", 5)["items"][0]["text"].endswith("final")


def test_demo_seed_is_explicit_and_complete(web_db):
    from cslurm.web.demo import seed
    seed()
    assert data.overview()["total"] == 51
    detail = data.detail("62084")
    assert "演示" in detail["job"]["summary"]["one_line_summary"]
    assert detail["events"] and detail["files"] and detail["commands"]
    assert "DEMO" in data.logs("62084")["items"][0]["text"]


def test_filter_normalizes_state_suffixes(web_db):
    with sqlite3.connect(web_db / "db.sqlite") as conn:
        conn.execute("update jobs set state='CANCELLED by 1234' where job_id='100'")
        conn.execute("update jobs set state='FAILED+' where job_id='101'")
    assert data.list_jobs(state="CANCELLED")["items"][0]["job_id"] == "100"
    assert data.list_jobs(state="failed")["total"] == 1
    assert data.overview()["failed"] == 1


def test_corrupt_database_disables_browsing_and_actions(server, web_db):
    server.database_error = "Database integrity check failed"
    status, overview = request(server, "/api/overview")
    assert status == 200
    assert overview["database_error"]
    assert overview["total"] is None
    assert request(server, "/api/jobs")[0] == 503
    assert request(server, "/api/operations", body={"action":"refresh"})[0] == 503
    assert server.operations.active is None
    assert request(server, "/", auth=False)[0] == 200


def test_integrity_check_detects_invalid_sqlite_without_overwriting(isolated_home):
    path = isolated_home / "db.sqlite"
    path.write_bytes(b"not a sqlite database")
    assert data.check_integrity()
    with ConsoleServer(0) as instance:
        assert instance.database_error
    assert path.read_bytes() == b"not a sqlite database"


def test_stopping_web_terminates_only_owned_helper_and_releases_lock(web_db, fake_bin):
    import fcntl
    from cslurm.web.operations import BusyError
    write_executable(fake_bin / "sacct", "#!/bin/sh\nsleep 60\n")
    operations = Operations()
    op = operations.start("refresh", {})
    deadline = time.monotonic() + 3
    while operations.process is None and time.monotonic() < deadline:
        time.sleep(.01)
    operations.close()
    assert operations.get(op["id"])["status"] == "failed"
    assert not operations.worker.is_alive()
    with (web_db / "ctrack.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with pytest.raises(BusyError):
        operations.start("refresh", {})


def test_ai_operations_use_configured_provider_and_persist_summaries(web_db, monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls = []

    class ModelHandler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            content = json.dumps({"one_line_summary": "Synthetic provider summary", "human_summary": "Synthetic completion"})
            payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    model = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("CSLURM_AI_BASE_URL", f"http://127.0.0.1:{model.server_port}")
    monkeypatch.setenv("CSLURM_AI_API_KEY", "test-only-key")
    monkeypatch.setenv("CSLURM_AI_MODEL", "test-model")
    monkeypatch.setenv("CSLURM_AI_FORMAT", "openai")
    operations = Operations()
    try:
        for kind in ["submission", "completion"]:
            op = operations.start("summarize", {"job_id":"100", "kind":kind})
            result = wait_operation(operations, op)
            assert result["status"] == "succeeded", result
        result = wait_operation(operations, operations.start("ask", {"question":"Which jobs finished?"}))
        assert result["status"] == "succeeded", result
        assert "Synthetic" in result["result"]
        job = data.get_job("100")
        assert job["summary"]["one_line_summary"] == "Synthetic provider summary"
        assert job["completion_summary"]["human_summary"] == "Synthetic completion"
        assert len(calls) == 3
        assert all(call["model"] == "test-model" for call in calls)
    finally:
        operations.close()
        model.shutdown()
        model.server_close()
        thread.join()
