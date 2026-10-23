"""Atomic command receipts shared by srun and scancel; never execute records."""

import json
import os
import socket
import uuid

from cslurm.config import root_dir


def publish(kind, record):
    directory = root_dir() / 'spool' / kind
    directory.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    temporary = directory / (key + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump({**record, 'record_id': key, 'hostname': socket.gethostname()}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    path = directory / (key + '.json')
    temporary.rename(path)
    return path


def archive(path, category='ingested'):
    directory = path.parent / category
    directory.mkdir(exist_ok=True)
    destination = directory / path.name
    if destination.exists():
        destination = directory / (path.stem + '.replay-' + uuid.uuid4().hex + '.json')
    try:
        path.rename(destination)
    except FileNotFoundError:
        pass


def pending(kind, validate, paths=None):
    paths = paths if paths is not None else sorted((root_dir() / 'spool' / kind).glob('*.json'))
    for path in paths:
        try:
            text = path.read_text(encoding='utf-8')
            record = json.loads(text)
        except FileNotFoundError:
            continue
        except (ValueError, UnicodeError):
            archive(path, 'invalid')
            continue
        if not isinstance(record, dict) or not validate(record):
            archive(path, 'invalid')
            continue
        yield path, record, text


def ingest_commands():
    from cslurm.db import connect, init_db
    from cslurm.runtime.spool import ingest as ingest_runs
    from cslurm.runtime.cancellation import ingest as ingest_cancellations
    # Avoid opening SQLite merely to inspect an empty command spool.
    if not any(next((root_dir() / 'spool' / kind).glob('*.json'), None)
               for kind in ('csrun', 'scancel')):
        return 0
    with connect() as conn:
        init_db(conn)
        return ingest_runs(conn) + ingest_cancellations(conn)
