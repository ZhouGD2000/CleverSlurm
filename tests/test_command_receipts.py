import json
import sqlite3

import pytest

from conftest import write_executable
from test_command_transparency import invoke


@pytest.mark.parametrize('args,expected', [
    (['12345', '67890'], [('12345', 'CANCEL_REQUESTED'), ('67890', 'CANCEL_REQUESTED')]),
    (['-s', '9', '12345'], [('12345', 'SIGNAL_REQUESTED')]),
    (['--signal=USR1', '12345'], [('12345', 'SIGNAL_REQUESTED')]),
    (['--', '12345_2', '12345.0'], [('12345_2', 'CANCEL_REQUESTED'), ('12345.0', 'SIGNAL_REQUESTED')]),
    (['--user', '12345', '67890'], [(None, 'SCANCEL_REQUESTED')]),
    (['-i', '12345'], [(None, 'SCANCEL_REQUESTED')]),
    (['--clusters', 'other', '12345'], [(None, 'SCANCEL_REQUESTED')]),
    (['12345_[1-3]'], [(None, 'SCANCEL_REQUESTED')]),
    (['--plugin-option', '12345', '67890'], [(None, 'SCANCEL_REQUESTED')]),
    ([], [(None, 'SCANCEL_REQUESTED')]),
])
def test_cancellation_audit_does_not_guess_or_change_states(isolated_home, fake_bin, monkeypatch, args, expected):
    from cslurm.db import connect, init_db
    from cslurm.runtime.receipts import ingest_commands
    write_executable(fake_bin / 'scancel', '#!/bin/sh\nexit 0\n')
    monkeypatch.setenv('CSLURM_CANCEL_NOTE', 'test note')
    with connect() as conn:
        init_db(conn)
        for job_id in ('12345', '67890'):
            conn.execute("insert into jobs(job_id,state,summary_json) values (?,'RUNNING','{}')", (job_id,))
        conn.commit()
    assert invoke('scancel', args).returncode == 0
    receipt = next((isolated_home / 'spool/scancel').glob('*.json'))
    original = receipt.read_bytes()
    assert ingest_commands() == 1
    receipt.write_bytes(original)
    assert ingest_commands() == 0
    with connect() as conn:
        assert [tuple(r) for r in conn.execute("select job_id,event_type from job_events where event_type!='SCANCEL_RECEIPT'")] == expected
        assert all(r[0] == 'test note' for r in conn.execute("select note from job_events where event_type!='SCANCEL_RECEIPT'"))
        assert all(tuple(r) == ('RUNNING', '{}') for r in conn.execute('select state,summary_json from jobs'))
        assert conn.execute('select count(*) from notifications').fetchone()[0] == 0
        assert conn.execute('select count(*) from work_items').fetchone()[0] == 0


def test_failed_request_is_audited_without_claiming_all_targets_failed(isolated_home, fake_bin):
    from cslurm.cli.cscancel import cancel_job
    write_executable(fake_bin / 'scancel', '#!/bin/sh\nprintf "partial failure\\n" >&2\nexit 7\n')
    result = cancel_job(['12345', '67890'])
    assert result.returncode == 7
    with sqlite3.connect(isolated_home / 'db.sqlite') as conn:
        assert conn.execute("select job_id,event_type from job_events where event_type!='SCANCEL_RECEIPT'").fetchall() == [(None, 'SCANCEL_FAILED')]


def test_environment_filters_are_not_ignored_in_audit(isolated_home, fake_bin, monkeypatch):
    from cslurm.cli.cscancel import cancel_job
    monkeypatch.setenv('SCANCEL_USER', '12345')
    write_executable(fake_bin / 'scancel', '#!/bin/sh\nexit 0\n')
    assert cancel_job(['67890']).returncode == 0
    with sqlite3.connect(isolated_home / 'db.sqlite') as conn:
        assert conn.execute("select job_id,event_type from job_events where event_type!='SCANCEL_RECEIPT'").fetchall() == [(None, 'SCANCEL_REQUESTED')]


@pytest.mark.parametrize('name', ['srun', 'scancel'])
@pytest.mark.parametrize('args', [['--help'], ['--version'], ['--usage']])
def test_information_calls_do_not_create_job_or_request_records(isolated_home, fake_bin, name, args):
    write_executable(fake_bin / name, '#!/bin/sh\nprintf "help\\n"\n')
    assert invoke(name, args).returncode == 0
    assert not (isolated_home / 'spool').exists()
    assert not (isolated_home / 'db.sqlite').exists()


def test_srun_completion_time_is_not_launch_time(isolated_home, fake_bin):
    write_executable(fake_bin / 'srun', '#!/bin/sh\nsleep .1\nexit 7\n')
    assert invoke('srun', ['true']).returncode == 7
    receipt = next((isolated_home / 'spool/csrun').glob('*.json'))
    record = json.loads(receipt.read_text())
    assert record['started_at'] < record['time']
    from cslurm.runtime.receipts import ingest_commands
    assert ingest_commands() == 1
    with sqlite3.connect(isolated_home / 'db.sqlite') as conn:
        assert conn.execute('select submitted_at,updated_at,state from jobs').fetchone() == (record['started_at'], record['time'], 'FAILED')


def test_command_receipts_are_consumed_by_worker(isolated_home, fake_bin):
    write_executable(fake_bin / 'srun', '#!/bin/sh\nexit 0\n')
    write_executable(fake_bin / 'scancel', '#!/bin/sh\nexit 0\n')
    assert invoke('srun', ['true']).returncode == 0
    assert invoke('scancel', ['12345']).returncode == 0
    from cslurm.work import drain
    assert drain() == 0
    with sqlite3.connect(isolated_home / 'db.sqlite') as conn:
        assert conn.execute('select count(*) from jobs').fetchone()[0] == 1
        assert conn.execute("select count(*) from job_events where event_type='SRUN_FINISHED'").fetchone()[0] == 1
        assert conn.execute("select count(*) from job_events where event_type='CANCEL_REQUESTED'").fetchone()[0] == 1


@pytest.mark.parametrize('kind,field,value', [
    ('csrun', 'wrapper_command', []), ('csrun', 'started_at', {}),
    ('scancel', 'note', []), ('scancel', 'stdout', None),
    ('scancel', 'audit', {'events': [[None, []]]}),
])
def test_invalid_receipt_does_not_block_next_record(isolated_home, fake_bin, kind, field, value):
    from cslurm.runtime.receipts import ingest_commands
    name = 'srun' if kind == 'csrun' else 'scancel'
    write_executable(fake_bin / name, '#!/bin/sh\nexit 0\n')
    assert invoke(name, ['12345']).returncode == 0
    path = next((isolated_home / 'spool' / kind).glob('*.json'))
    malformed = json.loads(path.read_text())
    malformed[field] = value
    invalid = path.with_name('000-invalid.json')
    invalid.write_text(json.dumps(malformed))
    assert ingest_commands() == 1
    assert (path.parent / 'invalid' / invalid.name).exists()
    assert (path.parent / 'ingested' / path.name).exists()
