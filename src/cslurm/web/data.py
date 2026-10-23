import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

from cslurm.config import db_path
from cslurm.storage import require_local_database, UnsafeDatabaseError
from cslurm.notify.analysis import normalize_state
from cslurm.slurm.tracker import TERMINAL_STATES


ACTIVE_STATES = {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED", "REQUEUED", "RESIZING"}
FAILED_STATES = TERMINAL_STATES - {"COMPLETED", "CANCELLED"}
MAX_LOG_BYTES = 128 * 1024


def partition_names(value):
    return {name.strip() for name in (value or "").split(",") if name.strip()}


@contextmanager
def reader():
    # GET requests never migrate the schema or change SQLite's journal mode.
    require_local_database()
    conn = sqlite3.connect(db_path().resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.create_function("normalized_state", 1, normalize_state, deterministic=True)
    conn.create_function("partition_matches", 2,
                         lambda actual, selected: bool(partition_names(actual) & partition_names(selected)),
                         deterministic=True)
    try:
        yield conn
    finally:
        conn.close()


def decode(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value


def check_integrity():
    try:
        with reader() as conn:
            result = conn.execute("pragma quick_check").fetchone()
        if result and result[0] == "ok":
            return None
    except (sqlite3.Error, UnsafeDatabaseError):
        pass
    return "Database integrity check failed or database is unavailable. Back up and repair it on the server before enabling operations."


def job_record(row):
    job = dict(row)
    job["state"] = normalize_state(job.get("state"))
    for name in ("summary_json", "completion_summary_json"):
        if name in job:
            job[name.removesuffix("_json")] = decode(job.pop(name))
    return job


def get_job(job_id):
    with reader() as conn:
        row = conn.execute("select * from jobs where job_id = ?", (job_id,)).fetchone()
    if row is None:
        raise KeyError("Job not found")
    return job_record(row)


def list_jobs(*, search="", state="", partition="", limit=25, offset=0):
    conditions, values = [], []
    if search:
        conditions.append("(instr(lower(job_id), ?) > 0 or instr(lower(coalesce(job_name, '')), ?) > 0 or instr(lower(coalesce(command, '')), ?) > 0)")
        values.extend([search.lower()] * 3)
    states = set()
    for selected in state.split(","):
        selected = selected.strip()
        if selected:
            states.update(ACTIVE_STATES if selected == "active" else FAILED_STATES if selected == "failed" else {selected})
    if states:
        conditions.append("normalized_state(state) in (" + ",".join("?" for _ in states) + ")")
        values.extend(sorted(states))
    if partition_names(partition):
        conditions.append("partition_matches(partition, ?)")
        values.append(partition)
    where = " where " + " and ".join(conditions) if conditions else ""
    with reader() as conn:
        total = conn.execute("select count(*) from jobs" + where, values).fetchone()[0]
        rows = conn.execute(
            "select job_id, job_name, partition, user, account, state, exit_code, elapsed, max_rss, "
            "nodelist, nodes, alloc_cpus, submitted_at, updated_at, command from jobs" + where
            + " order by submitted_at desc, job_id desc limit ? offset ?", (*values, limit, offset),
        ).fetchall()
    return {"items": [job_record(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def overview():
    with reader() as conn:
        counts = {}
        for row in conn.execute("select state, count(*) as n from jobs group by state"):
            state = normalize_state(row["state"])
            counts[state] = counts.get(state, 0) + row["n"]
        partitions = sorted({name for row in conn.execute("select distinct partition from jobs")
                             for name in partition_names(row[0])})
        updated_at = conn.execute("select max(updated_at) from jobs").fetchone()[0]
    return {
        "total": sum(counts.values()), "active": sum(counts.get(s, 0) for s in ACTIVE_STATES),
        "completed": counts.get("COMPLETED", 0), "failed": sum(counts.get(s, 0) for s in FAILED_STATES),
        "states": counts, "partitions": partitions, "updated_at": updated_at,
    }


def detail(job_id):
    job = get_job(job_id)
    result = {"job": job}
    with reader() as conn:
        for key, table in (("events", "job_events"), ("files", "job_files"), ("commands", "job_commands"), ("notifications", "notifications"), ("analysis", "job_analysis")):
            rows = conn.execute(f"select * from {table} where job_id = ? order by id desc limit 201", (job_id,)).fetchall()
            result[key] = [dict(row) for row in rows[:200]]
            result[key + "_truncated"] = len(rows) > 200
    # Always include submission and log paths even before static analysis finishes.
    known = {row["path"] for row in result["files"]}
    for field, role in (("original_script_path", "original_script"), ("copied_script_path", "copied_script"), ("stdout_path", "stdout"), ("stderr_path", "stderr")):
        path = job.get(field)
        if path and path not in known:
            result["files"].append({"path": path, "role": role, "source": "submission", "size": None})
            known.add(path)
    return result


def tail(path, lines):
    fd = None
    try:
        # Do not block on a FIFO/device supplied as a log path.
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return {"text": "", "error": "Not a regular file", "truncated": False}
        start = max(0, info.st_size - MAX_LOG_BYTES)
        os.lseek(fd, start, os.SEEK_SET)
        content = os.read(fd, MAX_LOG_BYTES).decode("utf-8", errors="replace")
        parts = content.splitlines()
        if start and parts:
            parts = parts[1:]
        return {"text": "\n".join(parts[-lines:]), "error": None, "truncated": bool(start or len(parts) > lines)}
    except OSError:
        return {"text": "", "error": "File unavailable on this host", "truncated": False}
    finally:
        if fd is not None:
            os.close(fd)


def logs(job_id, lines=200):
    job = get_job(job_id)
    base = Path(job.get("effective_chdir") or job.get("submit_cwd") or ".").expanduser()
    paths = [(name, job.get(name + "_path")) for name in ("stdout", "stderr")]
    if not any(path for _, path in paths) and (job.get("effective_chdir") or job.get("submit_cwd")):
        paths = [("stdout (inferred)", str(base / f"slurm-{job_id}.out"))]
    results = []
    for stream, raw in paths:
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = base / path
        results.append({"stream": stream, "path": str(path), **tail(path, lines)})
    return {"items": results, "lines": lines, "max_bytes_per_file": MAX_LOG_BYTES}
