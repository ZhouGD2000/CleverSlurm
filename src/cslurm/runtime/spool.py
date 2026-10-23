"""Durable srun completion handoff without opening SQLite on compute nodes."""

from cslurm.runtime.receipts import archive, pending, publish as publish_receipt


def publish(record: dict):
    return publish_receipt("csrun", record)


def _valid(record):
    return (all(isinstance(record.get(k), str) for k in
                ("job_id", "time", "cwd", "command", "state", "exit_code"))
            and isinstance(record.get("allocation"), bool)
            and all(k not in record or isinstance(record[k], str)
                    for k in ("started_at", "wrapper_command")))


def ingest(conn, *, paths=None) -> int:
    count = 0
    for path, record, text in pending("csrun", _valid, paths):
        key = "csrun-spool:" + path.stem
        conn.execute("begin immediate")
        try:
            if not conn.execute("select 1 from job_events where event_type='SRUN_FINISHED' and note=?",
                                (key,)).fetchone():
                # A step must not replace its batch job or mark the allocation finished.
                conn.execute(
                    "insert or ignore into jobs (job_id,submitted_at,submit_cwd,command,state,"
                    "exit_code,created_at,updated_at) values (?,?,?,?,?,?,?,?)",
                    (record["job_id"], record.get("started_at", record["time"]), record["cwd"],
                     record.get("wrapper_command", record["command"]),
                     "UNKNOWN" if record["allocation"] else record["state"],
                     None if record["allocation"] else record["exit_code"], record["time"], record["time"]),
                )
                conn.execute(
                    "insert into job_events(job_id,event_time,event_type,command,cwd,note,raw_output) "
                    "values (?,?,'SRUN_FINISHED',?,?,?,?)",
                    (record["job_id"], record["time"], record["command"], record["cwd"], key, text),
                )
                count += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        archive(path)
    return count
