import sqlite3

from cslurm.db import connect, run_write_transaction


def test_connect_configures_busy_timeout_and_wal(isolated_home):
    with connect() as conn:
        busy_timeout = conn.execute("pragma busy_timeout").fetchone()[0]
        journal_mode = conn.execute("pragma journal_mode").fetchone()[0]

    assert busy_timeout >= 30000
    assert journal_mode.lower() == "wal"


def test_connect_allows_busy_timeout_override(isolated_home, monkeypatch):
    monkeypatch.setenv("CSLURM_DB_BUSY_TIMEOUT_MS", "12000")

    with connect() as conn:
        busy_timeout = conn.execute("pragma busy_timeout").fetchone()[0]

    assert busy_timeout == 12000


def test_run_write_transaction_retries_locked_errors(isolated_home):
    calls = 0

    def write(conn):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("database is locked")
        conn.execute("insert into job_events (job_id, event_time, event_type) values ('1', 't', 'OK')")

    run_write_transaction(write, retry_seconds=1, sleep=lambda _: None)

    with connect() as conn:
        count = conn.execute("select count(*) from job_events where event_type = 'OK'").fetchone()[0]

    assert calls == 2
    assert count == 1


def test_run_write_transaction_does_not_retry_other_operational_errors(isolated_home):
    calls = 0

    def write(conn):
        nonlocal calls
        calls += 1
        raise sqlite3.OperationalError("syntax error")

    try:
        run_write_transaction(write, retry_seconds=1, sleep=lambda _: None)
    except sqlite3.OperationalError as exc:
        assert str(exc) == "syntax error"
    else:
        raise AssertionError("expected OperationalError")

    assert calls == 1
