"""Cancellation request audit only: ingestion never calls Slurm or changes job state."""

from cslurm.runtime.receipts import archive, pending, publish as publish_receipt


def publish(record):
    return publish_receipt('scancel', record)


def _valid(record):
    return (all(isinstance(record.get(k), str) for k in ('time', 'cwd', 'command'))
            and isinstance(record.get('returncode'), int)
            and (record.get('note') is None or isinstance(record['note'], str))
            and all(isinstance(record.get(k, ''), str) for k in ('stdout', 'stderr'))
            and isinstance(record.get('audit'), dict)
            and isinstance(record['audit'].get('events'), list)
            and all(isinstance(e, list) and len(e) == 2
                    and (e[0] is None or isinstance(e[0], str))
                    and isinstance(e[1], str)
                    and e[1] in {'CANCEL_REQUESTED', 'SIGNAL_REQUESTED', 'SCANCEL_REQUESTED'}
                    for e in record['audit']['events']))


def ingest(conn, *, paths=None):
    count = 0
    for path, record, text in pending('scancel', _valid, paths):
        key = 'scancel-spool:' + path.stem
        conn.execute('begin immediate')
        try:
            if not conn.execute("select 1 from job_events where event_type='SCANCEL_RECEIPT' and note=?", (key,)).fetchone():
                conn.execute("insert into job_events(event_time,event_type,note,raw_output) "
                             "values (?,'SCANCEL_RECEIPT',?,?)", (record['time'], key, text))
                events = record['audit']['events'] if record['returncode'] == 0 else [[None, 'SCANCEL_FAILED']]
                for job_id, event in events:
                    conn.execute('insert into job_events(job_id,event_time,event_type,command,cwd,note,raw_output) '
                                 'values (?,?,?,?,?,?,?)',
                                 (job_id, record['time'], event, record['command'], record['cwd'],
                                  record.get('note'), record.get('stdout', '') + record.get('stderr', '')))
                count += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        archive(path)
    return count
