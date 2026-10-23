import sqlite3
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from cslurm import storage
from cslurm.db import connect, init_db, run_write_transaction
from cslurm.runtime import spool
from conftest import write_executable


def test_mountinfo_longest_match_and_escaped_path(monkeypatch):
    monkeypatch.setattr(storage.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_text", lambda self: (
        "1 0 8:1 / / rw - ext4 /dev/a rw\n"
        "2 1 0:1 / /shared\\040home rw - nfs4 server:/home rw\n"
        "3 2 8:2 / /shared\\040home/local rw - xfs /dev/b rw\n"
    ))
    assert storage.filesystem_type(Path('/shared home/db.sqlite')) == 'nfs4'
    assert storage.filesystem_type(Path('/shared home/local/db.sqlite')) == 'xfs'
    assert storage.filesystem_type(Path('/shared homes/db.sqlite')) == 'ext4'


def test_network_mount_refused_before_sqlite_open(isolated_home, monkeypatch):
    monkeypatch.setattr(storage, "filesystem_type", lambda _: "nfs4")
    with pytest.raises(storage.UnsafeDatabaseError, match="management host"):
        connect()
    assert not (isolated_home / 'db.sqlite').exists()
    from cslurm.web.data import reader
    with pytest.raises(storage.UnsafeDatabaseError):
        with reader():
            pass


def test_csbatched_job_not_submitted_on_network_database(isolated_home, monkeypatch):
    from cslurm.cli import csbatch
    monkeypatch.setattr(storage, 'filesystem_type', lambda _: 'nfs4')
    monkeypatch.setattr(csbatch, 'run_slurm_command', lambda *a: pytest.fail('submitted a job'))
    with pytest.raises(storage.UnsafeDatabaseError):
        csbatch.submit_batch_result(['--wrap=true'])


def test_legacy_wal_requires_explicit_offline_migration(isolated_home):
    path = isolated_home / 'db.sqlite'
    with sqlite3.connect(path) as conn:
        conn.execute('pragma journal_mode=wal')
        conn.execute('create table preserved (value text)')
        conn.execute("insert into preserved values ('original')")
    conn.close()
    with pytest.raises(RuntimeError, match='Legacy WAL'):
        connect()
    with sqlite3.connect(path) as conn:
        assert conn.execute('select value from preserved').fetchone()[0] == 'original'
        assert conn.execute('pragma journal_mode').fetchone()[0] == 'wal'
    conn.close()


def test_connection_closes_after_context_and_uses_full_sync(isolated_home):
    with connect() as conn:
        assert conn.execute('pragma synchronous').fetchone()[0] == 2
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('select 1')


def test_compute_csrun_spools_without_database_and_ingests_once(isolated_home, fake_bin, monkeypatch):
    from cslurm.cli.csrun import run_job
    write_executable(fake_bin / 'srun', '#!/bin/sh\nprintf "out\\n"\nexit 7\n')
    monkeypatch.setenv('SLURM_JOB_ID', '123')
    monkeypatch.setattr(storage, 'filesystem_type', lambda _: 'nfs4')
    result = run_job(['true'])
    assert result.returncode == 7 and result.stdout == 'out\n'
    assert not (isolated_home / 'db.sqlite').exists()
    pending = list((isolated_home / 'spool/csrun').glob('*.json'))
    assert len(pending) == 1
    original = pending[0].read_bytes()
    monkeypatch.setattr(storage, 'filesystem_type', lambda _: 'xfs')
    with connect() as conn:
        init_db(conn)
        conn.execute("insert into jobs (job_id,state,job_name,summary_json) values ('123','RUNNING','batch','{}')")
        conn.commit()
        assert spool.ingest(conn) == 1
        # Simulate replay following a crash after DB commit but before archival.
        pending[0].write_bytes(original)
        assert spool.ingest(conn) == 0
        assert conn.execute("select count(*) from job_events").fetchone()[0] == 1
        row = conn.execute("select state,job_name,summary_json from jobs").fetchone()
        assert tuple(row) == ('RUNNING', 'batch', '{}')
    assert (pending[0].parent / 'ingested' / pending[0].name).read_bytes() == original


def _write_many(_):
    for i in range(15):
        run_write_transaction(lambda conn: conn.execute(
            "insert into job_events (job_id,event_type) values ('123','CONCURRENT_TEST')"))


def test_multiprocess_rollback_writers(isolated_home):
    with connect() as conn:
        init_db(conn)
    with ProcessPoolExecutor(max_workers=4) as pool:
        list(pool.map(_write_many, range(4)))
    with connect() as conn:
        assert conn.execute('pragma integrity_check').fetchone()[0] == 'ok'
        assert conn.execute('select count(*) from job_events').fetchone()[0] == 60


def test_array_parent_accounting_uses_all_children():
    from cslurm.slurm.tracker import accounting_row
    rows = {'123_0': {'State': 'COMPLETED', 'ExitCode': '0:0'},
            '123_1': {'State': 'RUNNING', 'ExitCode': '0:0'},
            '123_[2-9]': {'State': 'PENDING', 'ExitCode': '0:0'},
            '1234_0': {'State': 'FAILED', 'ExitCode': '1:0'}}
    assert accounting_row(rows, '123')['State'] == 'RUNNING'
    rows['123_1']['State'] = 'FAILED'
    assert accounting_row(rows, '123')['State'] == 'PENDING'
    rows['123_[2-9]']['State'] = 'COMPLETED'
    assert accounting_row(rows, '123')['State'] == 'FAILED'
    assert accounting_row(rows, '999') is None
