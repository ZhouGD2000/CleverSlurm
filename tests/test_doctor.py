import json
import sqlite3

import pytest

from cslurm import doctor
from cslurm.db import connect, init_db
from cslurm.notify.dispatcher import enqueue_job_notification


def test_backup_restore_never_overwrites_and_verifies(isolated_home, tmp_path):
    with connect() as conn:
        init_db(conn)
        conn.execute("insert into jobs(job_id,state) values ('existing','COMPLETED')")
    target = tmp_path / 'snapshot.sqlite'
    assert doctor.backup(target)['jobs'] == 1
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        doctor.backup(target)
    assert original == target.read_bytes()
    restored = tmp_path / 'restored'
    assert doctor.restore(target, restored)['jobs'] == 1
    with pytest.raises(FileExistsError):
        doctor.restore(target, restored)
    assert (isolated_home / 'db.sqlite').exists()


def test_freeze_boundary_suppresses_history_but_not_future(isolated_home):
    with connect() as conn:
        init_db(conn)
        conn.executemany('insert into jobs(job_id,state) values (?,?)', [('old','COMPLETED'), ('unknown','UNKNOWN'), ('active','RUNNING')])
        conn.execute("insert into notifications(job_id,status) values ('old','pending')")
    assert doctor.freeze_history()['suppressed'] == 1
    with connect() as conn:
        for job in ['old','unknown','active','new']:
            enqueue_job_notification(conn, {'job_id':job,'recommended_notification':'immediate','semantic_status':'failed'})
        assert [r[0] for r in conn.execute("select distinct status from notifications where job_id in ('old','unknown')")] == ['suppressed']
        assert [r[0] for r in conn.execute("select distinct status from notifications where job_id in ('active','new')")] == ['pending']


def test_health_read_only_and_query_indexes(isolated_home, monkeypatch):
    with connect() as conn:
        init_db(conn)
        conn.execute("insert into service_health(name,succeeded_at) values ('tracker','2000-01-01T00:00:00+00:00')")
        events = [r[3] for r in conn.execute("explain query plan select * from job_events where job_id='1' order by id desc limit 200")]
        jobs = [r[3] for r in conn.execute('explain query plan select job_id from jobs order by submitted_at desc,job_id desc limit 25')]
    monkeypatch.setattr('cslurm.auto_track._read_crontab', lambda: '')
    before = (isolated_home / 'db.sqlite').read_bytes()
    status = doctor.health()
    assert status['tracker_stale'] and status['database_error'] is None
    assert (isolated_home / 'db.sqlite').read_bytes() == before
    assert 'events_job_idx' in str(events)
    assert 'jobs_submitted_idx' in str(jobs) and 'TEMP B-TREE' not in str(jobs)
