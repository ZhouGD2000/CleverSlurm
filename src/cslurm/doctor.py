"""Read-only health checks and non-overwriting database backup/restore."""
import fcntl
import os
import shutil
import sqlite3
import subprocess
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from cslurm.config import db_path, root_dir
from cslurm.db import run_write_transaction
from cslurm.storage import require_local_database


def health():
    from cslurm import auto_track
    from cslurm.web.data import reader, check_integrity
    result = {'database_error': check_integrity(), 'tracker': {}, 'work': [], 'calls': [], 'notifications': {}, 'queue': {}}
    try:
        result['cron_enabled'] = auto_track.status().enabled
    except (OSError, TimeoutError, subprocess.TimeoutExpired):
        result['cron_enabled'] = None
    if not result['database_error']:
        with reader() as conn:
            tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
            if 'service_health' in tables:
                row = conn.execute("select * from service_health where name='tracker'").fetchone()
                result['tracker'] = dict(row) if row else {}
            if 'work_items' in tables:
                result['work'] = [dict(r) for r in conn.execute('select * from work_items order by id desc limit 100')]
                result['queue'] = dict(conn.execute('select status,count(*) from work_items group by status').fetchall())
            if 'model_calls' in tables:
                result['calls'] = [dict(r) for r in conn.execute('select * from model_calls order by id desc limit 100')]
            result['notifications'] = dict(conn.execute('select status,count(*) from notifications group by status').fetchall())
    timestamp = result['tracker'].get('succeeded_at')
    result['tracker_age_seconds'] = ((datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds() if timestamp else None)
    result['tracker_stale'] = result['tracker_age_seconds'] is None or result['tracker_age_seconds'] > 300
    result['disk_free_bytes'] = shutil.disk_usage(root_dir() if root_dir().exists() else root_dir().parent).free
    result['database_bytes'] = db_path().stat().st_size if db_path().exists() else 0
    result['slurm_commands'] = {name: bool(shutil.which(name)) for name in ['sacct', 'sbatch', 'srun', 'scancel']}
    return result


def verify(path):
    path = Path(path).expanduser().resolve()
    require_local_database(path)
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        integrity = [r[0] for r in conn.execute('pragma integrity_check')]
        if integrity != ['ok']:
            raise ValueError('Database integrity check failed')
        tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
        if not {'jobs', 'job_events', 'notifications'}.issubset(tables):
            raise ValueError('Not a CleverSlurm database')
        return {'path': str(path), 'integrity': 'ok', 'jobs': conn.execute('select count(*) from jobs').fetchone()[0]}
    finally:
        conn.close()


def backup(destination=None, *, source=None):
    source = Path(source or db_path()).expanduser().resolve()
    require_local_database(source)
    if destination is None:
        directory = root_dir() / 'backups'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8] + '.sqlite')
    destination = Path(destination).expanduser().absolute()
    require_local_database(destination)
    # O_EXCL refuses existing files and symlinks. Partial failed backups are retained.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    src = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    dst = sqlite3.connect(destination)
    try:
        src.backup(dst, pages=128, sleep=.01)
    finally:
        dst.close()
        src.close()
    with destination.open('rb') as stream:
        os.fsync(stream.fileno())
    return verify(destination)


def restore(source, destination):
    verify(source)
    destination = Path(destination).expanduser().absolute()
    require_local_database(destination / 'db.sqlite')
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    return backup(destination / 'db.sqlite', source=source)


def _freeze_history_records(conn):
    from cslurm.web.data import ACTIVE_STATES
    from cslurm.notify.analysis import normalize_state
    conn.execute('begin immediate')
    conn.create_function('normalized_state', 1, normalize_state)
    conn.execute('insert or ignore into notification_exclusions(job_id,reason,created_at) '
                 "select job_id,'Historical boundary',? from jobs where normalized_state(state) not in (" + ','.join('?' for _ in ACTIVE_STATES) + ')',
                 (datetime.now(timezone.utc).isoformat(), *ACTIVE_STATES))
    count = conn.execute("update notifications set status='suppressed',last_error='Historical boundary: no replay' "
                         "where status in ('pending','failed','sending','uncertain')").rowcount
    conn.execute("update notification_batches set status='suppressed' where status in ('pending','failed','sending','uncertain')")
    return {'suppressed': count, 'excluded_jobs': conn.execute('select count(*) from notification_exclusions').fetchone()[0]}


def freeze_history():
    """Explicit migration boundary. Never re-enable any existing notification."""
    with ExitStack() as stack:
        for name in ['ctrack.lock', 'workers.lock', 'dispatch.lock']:
            lock = stack.enter_context((root_dir() / name).open('a'))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run_write_transaction(_freeze_history_records)
