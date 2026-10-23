"""Durable, idempotent csrun handoff without opening SQLite on compute nodes."""

import json
import os
import socket
import uuid

from cslurm.config import root_dir


def publish(record: dict) -> None:
    directory = root_dir() / "spool" / "csrun"
    directory.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    temporary = directory / (key + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump({**record, "record_id": key, "hostname": socket.gethostname()}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.rename(directory / (key + ".json"))


def ingest(conn) -> int:
    directory = root_dir() / "spool" / "csrun"
    count = 0
    for path in sorted(directory.glob("*.json")):
        text = path.read_text(encoding="utf-8")
        record = json.loads(text)
        key = "csrun-spool:" + path.stem
        if not conn.execute("select 1 from job_events where event_type = 'SRUN_FINISHED' and note = ?", (key,)).fetchone():
            # A step must not replace the batch job or mark the whole allocation finished.
            conn.execute(
                "insert or ignore into jobs (job_id, submitted_at, submit_cwd, command, state, "
                "exit_code, created_at, updated_at) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (record["job_id"], record["time"], record["cwd"], record["command"],
                 "UNKNOWN" if record["allocation"] else record["state"],
                 None if record["allocation"] else record["exit_code"], record["time"], record["time"]),
            )
            conn.execute(
                "insert or ignore into job_events (job_id,event_time,event_type,command,cwd,note,raw_output) "
                "values (?, ?, 'SRUN_FINISHED', ?, ?, ?, ?)",
                (record["job_id"], record["time"], record["command"], record["cwd"], key, text),
            )
            conn.commit()
            count += 1
        # Retain originals; a crash before the move is harmless on the next pass.
        archive = directory / "ingested"
        archive.mkdir(exist_ok=True)
        destination = archive / path.name
        if destination.exists():
            destination = archive / (path.stem + ".replay-" + uuid.uuid4().hex + ".json")
        path.rename(destination)
    return count
