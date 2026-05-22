from datetime import datetime, timezone
from pathlib import Path
import sys

from cslurm.collect.static_script import insert_static_commands
from cslurm.config import root_dir, static_analysis_enabled
from cslurm.db import connect, init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_event(job_id: str, event_type: str, raw_output: str | None = None) -> None:
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, raw_output)
            values (?, ?, ?, ?)
            """,
            (job_id, _now(), event_type, raw_output),
        )
        conn.commit()


def record_static_analysis_failure(job_id: str, exc: Exception) -> None:
    _record_event(job_id, "STATIC_ANALYSIS_FAILED", f"{type(exc).__name__}: {exc}")


def record_static_analysis_queued(job_id: str, *, pid: int | None = None) -> None:
    _record_event(job_id, "STATIC_ANALYSIS_QUEUED", f"pid={pid}" if pid is not None else None)


def _existing_path(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    return path if path.exists() else None


def _submission_script_path(job_id: str, row) -> Path | None:
    copied_original = root_dir() / "jobs" / job_id / "original.slurm"
    if copied_original.exists():
        return copied_original
    original = _existing_path(row["original_script_path"])
    if original:
        return original
    return _existing_path(row["copied_script_path"])


def _script_dir(row) -> Path:
    if row["original_script_path"]:
        return Path(row["original_script_path"]).expanduser().resolve().parent
    if row["submit_cwd"]:
        return Path(row["submit_cwd"]).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_output_path(path_text: str, script_dir: Path) -> Path:
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = script_dir / path
    return path.resolve()


def _job_file_exists(conn, *, job_id: str, path: str, role: str, source: str) -> bool:
    row = conn.execute(
        """
        select 1 from job_files
        where job_id = ? and path = ? and role = ? and source = ?
        limit 1
        """,
        (job_id, path, role, source),
    ).fetchone()
    return row is not None


def _insert_known_file(conn, *, job_id: str, path: Path, role: str, source: str, confidence: float) -> bool:
    path_text = str(path)
    if _job_file_exists(conn, job_id=job_id, path=path_text, role=role, source=source):
        return False
    conn.execute(
        """
        insert into job_files (job_id, path, relpath, size, role, source, copied, confidence)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job_id,
            path_text,
            path.name,
            path.stat().st_size if path.exists() else None,
            role,
            source,
            0,
            confidence,
        ),
    )
    return True


def _insert_output_files(conn, *, job_id: str, row, script_dir: Path) -> int:
    inserted = 0
    stdout_text = row["stdout_path"] or str(script_dir / f"slurm-{job_id}.out")
    stderr_text = row["stderr_path"]
    output_paths = [("stdout", _resolve_output_path(stdout_text, script_dir))]
    if stderr_text:
        stderr_path = _resolve_output_path(stderr_text, script_dir)
        if stderr_path != output_paths[0][1]:
            output_paths.append(("stderr", stderr_path))
    for role, path in output_paths:
        if _insert_known_file(conn, job_id=job_id, path=path, role=role, source="sbatch", confidence=0.9):
            inserted += 1
    return inserted


def static_analyze_submission(job_id: str) -> str:
    if not static_analysis_enabled():
        return "disabled"

    with connect() as conn:
        init_db(conn)
        row = conn.execute(
            """
            select job_id, submitted_at, submit_cwd, original_script_path,
                   copied_script_path, stdout_path, stderr_path
            from jobs where job_id = ?
            """,
            (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"job not found: {job_id}")

        script_path = _submission_script_path(job_id, row)
        if script_path is None:
            raise FileNotFoundError(f"no submission script recorded for job {job_id}")

        script_dir = _script_dir(row)
        timestamp = row["submitted_at"] or _now()
        result = insert_static_commands(
            conn,
            job_id=job_id,
            script_text=script_path.read_text(errors="replace"),
            script_dir=script_dir,
            timestamp=timestamp,
        )
        inserted_files = result.files + _insert_output_files(conn, job_id=job_id, row=row, script_dir=script_dir)
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, raw_output)
            values (?, ?, ?, ?)
            """,
            (job_id, _now(), "STATIC_ANALYSIS_CREATED", f"commands={result.commands} files={inserted_files}"),
        )
        conn.commit()
    return "created"


def auto_static_analyze_submission(job_id: str) -> str:
    try:
        return static_analyze_submission(job_id)
    except Exception as exc:
        record_static_analysis_failure(job_id, exc)
        return "failed"


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m cslurm.collect.static_auto JOB_ID")
    auto_static_analyze_submission(sys.argv[1])


if __name__ == "__main__":
    main()
