import io
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from cslurm.db import connect, init_db
from cslurm.notify.feishu import dispatch_pending
from cslurm import work


def seed(mode='batch'):
    with connect() as conn:
        init_db(conn)
        conn.execute("insert into notifications(job_id,channel,mode,status,title,body,payload_json) "
                     "values ('1','feishu',?,'pending','test','test','{}')", (mode,))


def test_notification_send_has_no_write_lock(isolated_home, monkeypatch):
    monkeypatch.setenv('CSLURM_FEISHU_WEBHOOK', 'https://example.invalid/hook')
    seed()
    def send(request, timeout):
        with sqlite3.connect(isolated_home / 'db.sqlite', timeout=.05) as conn:
            conn.execute("insert into jobs(job_id) values ('unblocked')")
        return io.BytesIO(b'{"code":0}')
    assert dispatch_pending(mode='batch', force=True, urlopen=send) == 1


def test_parallel_dispatch_claims_once_and_preserves_uncertain(isolated_home, monkeypatch):
    monkeypatch.setenv('CSLURM_FEISHU_WEBHOOK', 'https://example.invalid/hook')
    seed('immediate')
    entered, release = threading.Event(), threading.Event()
    sent = []
    def send(request, timeout):
        sent.append(1)
        entered.set()
        assert release.wait(3)
        raise TimeoutError('Could already have been accepted')
    thread = threading.Thread(target=lambda: dispatch_pending(urlopen=send))
    thread.start()
    try:
        assert entered.wait(3)
        assert dispatch_pending(urlopen=send) == 0
    finally:
        release.set()
        thread.join(5)
    assert dispatch_pending(urlopen=send) == 0
    assert len(sent) == 1
    with connect() as conn:
        assert conn.execute('select status from notifications').fetchone()[0] == 'uncertain'


def test_interrupted_send_never_automatically_replayed(isolated_home, monkeypatch):
    monkeypatch.setenv('CSLURM_FEISHU_WEBHOOK', 'https://example.invalid/hook')
    seed()
    with connect() as conn:
        conn.execute("update notifications set status='sending'")
    assert dispatch_pending(mode='all', force=True, urlopen=lambda *a, **k: pytest.fail('Replayed')) == 0
    with connect() as conn:
        assert conn.execute('select status from notifications').fetchone()[0] == 'uncertain'


def test_completion_failure_is_retried_without_historical_scan(isolated_home, monkeypatch):
    from cslurm.slurm.tracker import track_once
    monkeypatch.setenv('CSLURM_NOTIFICATION_AUTO_DISPATCH', 'false')
    with connect() as conn:
        init_db(conn)
        conn.executemany('insert into jobs(job_id,state) values (?,?)',
                         [('1','RUNNING'), ('old','COMPLETED'), ('older','CANCELLED by 123')])
    monkeypatch.setattr('cslurm.slurm.tracker.run_slurm_command', lambda *a: SimpleNamespace(returncode=0, stdout='1|COMPLETED|0:0|0:0|00:01||node\n'))
    calls = []
    def handler(item):
        calls.append(item['job_id'])
        if len(calls) == 1:
            raise RuntimeError('temporary')
    monkeypatch.setattr(work, 'execute', handler)
    track_once()
    assert calls == []
    work.drain()
    with connect() as conn:
        assert conn.execute('select status from work_items').fetchone()[0] == 'pending'
        conn.execute('update work_items set available_at=0')
    track_once()
    work.drain()
    assert calls == ['1', '1']


def test_queue_crash_recovery_dedupe_and_static_before_summary(isolated_home):
    work.submit('static','1')
    work.submit('summary','1')
    work.submit('summary','1')
    with connect() as conn:
        conn.execute("update work_items set status='running',attempts=1 where kind='static'")
    calls = []
    work.drain(handler=lambda item: calls.append(item['kind']))
    assert calls == ['static', 'summary']
    with connect() as conn:
        assert conn.execute("select count(*) from work_items where status='succeeded'").fetchone()[0] == 2


def test_worker_concurrency_is_bounded_and_exhaustion_requires_explicit_retry(isolated_home):
    import threading
    import time
    guard = threading.Lock()
    active, peak = [0], [0]
    for index in range(8):
        work.submit('static', str(index))
    def handler(item):
        with guard:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(.02)
        with guard:
            active[0] -= 1
        raise RuntimeError('private error text')
    work.drain(handler=handler)
    assert peak[0] <= 2
    with connect() as conn:
        conn.execute("update work_items set attempts=4, available_at=0")
    work.drain(handler=handler)
    with connect() as conn:
        assert conn.execute("select count(*) from work_items where status='failed' and attempts=5").fetchone()[0] == 8
        item_id = conn.execute('select id from work_items order by id limit 1').fetchone()[0]
    assert work.drain(handler=handler) == 0
    work.retry(item_id)
    assert work.drain(handler=lambda item: None) == 1


@pytest.mark.parametrize('format', ['openai', 'anthropic'])
@pytest.mark.parametrize('key', ['model','messages','system','stream','max_tokens'])
def test_model_core_fields_cannot_be_overridden(format, key):
    from cslurm.ai.client import ModelClient
    with pytest.raises(ValueError, match='cannot override'):
        ModelClient(api_key='dummy', base_url='https://example.invalid', model='selected', format=format, extra_body={key:'bad'})


def test_call_metadata_is_private_and_uses_response_model(isolated_home):
    from cslurm.ai.client import ModelClient
    with connect() as conn:
        init_db(conn)
    client = ModelClient(api_key='private-secret', base_url='https://example.invalid', model='alias',
                         urlopen=lambda *a, **k: io.BytesIO(b'{"model":"actual-model","choices":[{"message":{"content":"private-reply"}}]}'))
    client.chat_raw([{'role':'user','content':'private-prompt'}])
    with connect() as conn:
        rows = [dict(r) for r in conn.execute('select * from model_calls')]
    assert rows[0]['requested_model'] == 'alias' and rows[0]['response_model'] == 'actual-model'
    assert all(secret not in json.dumps(rows) for secret in ['private-secret','private-reply','private-prompt'])
