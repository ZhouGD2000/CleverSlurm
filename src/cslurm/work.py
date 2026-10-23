"""Durable local work with a single bounded worker pool per database root."""

import fcntl
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from cslurm.config import load_config, root_dir
from cslurm.db import run_write_transaction


def now():
    return datetime.now(timezone.utc).isoformat()


def enqueue(conn, kind, job_id, event_id=None):
    if kind not in {"summary", "static", "completion"}:
        raise ValueError("Unknown work kind")
    key = f"{kind}:{job_id}:{event_id or ''}"
    conn.execute("insert or ignore into work_items(dedupe_key,kind,job_id,event_id,created_at,updated_at) values (?,?,?,?,?,?)",
                 (key, kind, job_id, event_id, now(), now()))
    return conn.execute("select id from work_items where dedupe_key = ?", (key,)).fetchone()[0]


def submit(kind, job_id):
    return run_write_transaction(lambda conn: enqueue(conn, kind, job_id))


def _claim():
    def claim(conn):
        conn.execute("begin immediate")
        row = conn.execute(
            "select * from work_items w where status = 'pending' and available_at <= ? "
            "and (kind != 'summary' or not exists (select 1 from work_items s where s.job_id = w.job_id "
            "and s.kind = 'static' and s.status in ('pending','running'))) order by id limit 1", (time.time(),)).fetchone()
        if row is None:
            return None
        conn.execute("update work_items set status='running', attempts=attempts+1, updated_at=? where id=?", (now(), row['id']))
        return {**dict(row), "attempts": row['attempts'] + 1}
    return run_write_transaction(claim)


def execute(item):
    if item['kind'] == 'static':
        from cslurm.collect.static_auto import static_analyze_submission
        static_analyze_submission(item['job_id'])
    elif item['kind'] == 'summary':
        from cslurm.ai.summarize import summarize_submission
        summarize_submission(item['job_id'])
    else:
        from cslurm.notify.dispatcher import process_job_completion_standalone
        process_job_completion_standalone(item['job_id'], event_id=item['event_id'])


def drain(*, limit=100, handler=None, lock_fd=None):
    root_dir().mkdir(parents=True, exist_ok=True)
    lock = os.fdopen(lock_fd, 'a') if lock_fd is not None else (root_dir() / 'workers.lock').open('a')
    with lock:
        if lock_fd is None:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return 0
        from cslurm.slurm.submissions import ingest as ingest_submissions
        ingest_submissions()
        def recover(conn):
            conn.execute("update work_items set status=case when attempts >= 5 then 'failed' else 'pending' end, "
                         "updated_at=?, last_error='Worker interrupted' where status='running'", (now(),))
        run_write_transaction(recover)
        concurrency = max(1, min(8, int(load_config().get('work', {}).get('concurrency', '2'))))
        budget, guard = [0], threading.Lock()
        def worker():
            while True:
                with guard:
                    if budget[0] >= limit:
                        return
                    item = _claim()
                    if item is None:
                        return
                    budget[0] += 1
                status, error, available = 'succeeded', None, 0
                try:
                    (handler or execute)(item)
                except Exception as exc:
                    status = 'failed' if item['attempts'] >= 5 else 'pending'
                    # Provider bodies may contain credentials. Store only a safe category.
                    error = getattr(exc, 'code', type(exc).__name__)
                    available = time.time() + min(3600, 30 * 2 ** (item['attempts'] - 1))
                run_write_transaction(lambda conn: conn.execute(
                    "update work_items set status=?,last_error=?,available_at=?,updated_at=? where id=?",
                    (status, error, available, now(), item['id'])))
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(worker) for _ in range(concurrency)]
            for future in futures:
                future.result()
        return budget[0]


def wake():
    if os.environ.get('CSLURM_WORKER_AUTOSTART', 'true').lower() == 'false':
        return None
    root_dir().mkdir(parents=True, exist_ok=True)
    lock = (root_dir() / 'workers.lock').open('a')
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        with (root_dir() / 'workers.log').open('ab') as log:
            process = subprocess.Popen([sys.executable, '-m', 'cslurm.work', str(lock.fileno())],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       pass_fds=(lock.fileno(),), start_new_session=True)
        return process.pid
    finally:
        # Do not LOCK_UN: the child inherits the same open file description.
        lock.close()


def retry(item_id):
    def update(conn):
        cursor = conn.execute("update work_items set status='pending',attempts=0,available_at=0,updated_at=?,last_error=null "
                              "where id=? and status='failed'", (now(), item_id))
        if cursor.rowcount != 1:
            raise ValueError('Only an existing failed work item can be retried')
    run_write_transaction(update)


def record_health(name, phase, error=None):
    if phase not in {'started_at', 'succeeded_at', 'failed_at'}:
        raise ValueError('Invalid health phase')
    def update(conn):
        conn.execute('insert or ignore into service_health(name) values (?)', (name,))
        conn.execute(f'update service_health set {phase}=?,error=? where name=?', (now(), error, name))
    run_write_transaction(update)


if __name__ == '__main__':
    drain(lock_fd=int(sys.argv[1]) if len(sys.argv) == 2 else None)
