from cslurm.db import connect


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
