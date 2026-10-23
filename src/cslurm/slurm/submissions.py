"""Durable submission receipts; native CLI execution never opens SQLite."""

import json
import os
import re
import uuid
from pathlib import Path

from cslurm.collect.git import GitMetadata, collect_git_metadata
from cslurm.config import ai_auto_summary_enabled, root_dir, static_analysis_enabled
from cslurm.db import run_write_transaction
from cslurm.slurm.sbatch_options import command_text


def publish(record):
    directory = root_dir() / 'spool' / 'sbatch'
    directory.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    temporary = directory / (key + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump({**record, 'record_id': key}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    destination = directory / (key + '.json')
    temporary.rename(destination)
    return destination


def _archive(path, category):
    archive = path.parent / category
    archive.mkdir(exist_ok=True)
    destination = archive / path.name
    if destination.exists():
        destination = archive / (path.stem + '.replay-' + uuid.uuid4().hex + '.json')
    try:
        path.rename(destination)
    except FileNotFoundError:
        # A concurrent tracker may already have archived this same receipt.
        pass


def _preserve(path, text):
    try:
        with path.open('x', encoding='utf-8', errors='surrogateescape') as stream:
            stream.write(text)
    except FileExistsError:
        pass


def _log_path(value, directory, job_id, name, user):
    if not value:
        return None
    # Leave array/node-specific templates unresolved for accounting enrichment.
    expanded = value.replace('%j', job_id).replace('%A', job_id)
    expanded = expanded.replace('%x', name or '').replace('%u', user or '')
    path = Path(expanded)
    return str(path if path.is_absolute() else directory / path)


def _record(record):
    from cslurm.work import enqueue
    job_id, timestamp = record['job_id'], record['time']
    cwd = Path(record['cwd'])
    metadata = record.get('metadata', {})
    directory = Path(metadata.get('chdir') or cwd)
    if not directory.is_absolute():
        directory = cwd / directory
    directory = directory.resolve()
    job_dir = root_dir() / 'jobs' / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    copied = None
    if record.get('script_text') is not None:
        copied = job_dir / 'original.slurm'
        _preserve(copied, record['script_text'])
    try:
        git = collect_git_metadata(cwd)
    except OSError:
        git = GitMetadata(None, False, '', '')
    _preserve(job_dir / 'git_status.txt', git.status)
    _preserve(job_dir / 'git.diff', git.diff)
    name, user = metadata.get('job-name'), record.get('user')
    stdout = _log_path(metadata.get('output'), directory, job_id, name, user)
    if not stdout:
        default = f'slurm-{job_id}_%a.out' if metadata.get('array') else f'slurm-{job_id}.out'
        stdout = str(directory / default)
    stderr = _log_path(metadata.get('error'), directory, job_id, name, user) or stdout
    command = command_text('csbatch', record['argv'])
    key = 'sbatch-spool:' + record['record_id']
    static, summary = static_analysis_enabled(), ai_auto_summary_enabled()

    def write(conn):
        conn.execute('begin immediate')
        if conn.execute("select 1 from job_events where event_type='SUBMITTED' and note=?", (key,)).fetchone():
            return False
        inserted = conn.execute(
            'insert or ignore into jobs (job_id,submitted_at,submit_cwd,effective_chdir,command,'
            'original_script_path,copied_script_path,user,job_name,partition,account,qos,array_spec,'
            'dependency,nodes,nodelist,stdout_path,stderr_path,git_commit,git_dirty,state,created_at,updated_at) '
            'values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (job_id, timestamp, str(cwd), str(directory), command, record.get('script_path'),
             str(copied) if copied else None, user, name, metadata.get('partition'), metadata.get('account'),
             metadata.get('qos'), metadata.get('array'), metadata.get('dependency'), metadata.get('nodes'),
             metadata.get('nodelist'), stdout, stderr, git.commit, int(git.dirty), 'UNKNOWN', timestamp, timestamp),
        ).rowcount
        conn.execute('insert into job_events(job_id,event_time,event_type,command,cwd,note,raw_output) '
                     "values (?,?,'SUBMITTED',?,?,?,?)",
                     (job_id, timestamp, command, str(cwd), key, record.get('stdout', '')))
        if inserted:
            if copied:
                conn.execute('insert into job_files(job_id,path,relpath,size,role,source,copied,confidence) '
                             "values (?,?,'original.slurm',?,'original_script','csbatch',1,1.0)",
                             (job_id, str(copied), copied.stat().st_size))
            # Enqueue atomically with the job. A crash cannot lose these work items.
            for enabled, kind, event in ((static and copied is not None, 'static', 'STATIC_ANALYSIS_QUEUED'),
                                          (summary, 'summary', 'AI_SUMMARY_QUEUED')):
                if enabled:
                    enqueue(conn, kind, job_id)
                    conn.execute('insert into job_events(job_id,event_time,event_type) values (?,?,?)',
                                 (job_id, timestamp, event))
        return True

    return run_write_transaction(write)


def _valid_record(record):
    if not isinstance(record, dict):
        return False
    if not all(isinstance(record.get(key), str) for key in ('record_id', 'cwd', 'time', 'stdout')):
        return False
    if not re.fullmatch(r'[0-9a-f]{32}', record['record_id']) or not Path(record['cwd']).is_absolute():
        return False
    if record.get('job_id') is not None and (
            not isinstance(record['job_id'], str) or not re.fullmatch(r'[0-9]+', record['job_id'])):
        return False
    if not isinstance(record.get('argv'), list) or not all(isinstance(v, str) for v in record['argv']):
        return False
    if not isinstance(record.get('metadata'), dict):
        return False
    if not all(v is None or isinstance(v, str) for v in record['metadata'].values()):
        return False
    return all(record.get(key) is None or isinstance(record[key], str)
               for key in ('cluster', 'script_path', 'script_text', 'user'))


def ingest(*, paths=None):
    count = 0
    paths = paths if paths is not None else sorted((root_dir() / 'spool' / 'sbatch').glob('*.json'))
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            continue
        except (ValueError, UnicodeError):
            _archive(path, 'invalid')
            continue
        if not _valid_record(record):
            _archive(path, 'invalid')
            continue
        # Cross-cluster IDs cannot safely share the current job_id primary key.
        if not record.get('job_id') or record.get('cluster'):
            _archive(path, 'unresolved')
            continue
        count += int(_record(record))
        _archive(path, 'ingested')
    return count
