import sqlite3
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from cslurm.config import db_path, root_dir
from cslurm.storage import require_local_database


SCHEMA = """
CREATE TABLE IF NOT EXISTS work_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dedupe_key TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL,
  job_id TEXT NOT NULL,
  event_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS service_health (
  name TEXT PRIMARY KEY, started_at TEXT, succeeded_at TEXT, failed_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS notification_exclusions (
  job_id TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, format TEXT,
  requested_model TEXT, response_model TEXT, elapsed_ms INTEGER,
  status TEXT, error_code TEXT, fallback INTEGER
);
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  submitted_at TEXT,
  submit_cwd TEXT,
  effective_chdir TEXT,
  command TEXT,
  original_script_path TEXT,
  copied_script_path TEXT,
  user TEXT,
  job_name TEXT,
  partition TEXT,
  account TEXT,
  qos TEXT,
  array_spec TEXT,
  dependency TEXT,
  stdout_path TEXT,
  stderr_path TEXT,
  git_commit TEXT,
  git_dirty INTEGER,
  state TEXT,
  exit_code TEXT,
  derived_exit_code TEXT,
  reason TEXT,
  elapsed TEXT,
  max_rss TEXT,
  nodes TEXT,
  alloc_cpus INTEGER,
  nodelist TEXT,
  summary_json TEXT,
  completion_summary_json TEXT,
  tags TEXT,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  event_time TEXT,
  event_type TEXT,
  command TEXT,
  cwd TEXT,
  note TEXT,
  raw_output TEXT
);

CREATE TABLE IF NOT EXISTS job_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  step_id TEXT,
  time TEXT,
  hostname TEXT,
  cwd TEXT,
  kind TEXT,
  executable TEXT,
  argv TEXT,
  entry_file TEXT,
  entry_file_abs TEXT,
  source TEXT
);

CREATE TABLE IF NOT EXISTS job_files (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  command_id INTEGER,
  path TEXT,
  relpath TEXT,
  sha256 TEXT,
  size INTEGER,
  role TEXT,
  source TEXT,
  copied INTEGER,
  confidence REAL
);

CREATE TABLE IF NOT EXISTS job_analysis (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  created_at TEXT,
  slurm_state TEXT,
  exit_code TEXT,
  derived_exit_code TEXT,
  hard_failed INTEGER,
  deterministic_status TEXT,
  semantic_status TEXT,
  failure_category TEXT,
  severity TEXT,
  confidence REAL,
  evidence_json TEXT,
  ai_analysis_json TEXT,
  recommended_notification TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT,
  event_id INTEGER,
  group_id TEXT,
  created_at TEXT,
  severity TEXT,
  category TEXT,
  channel TEXT,
  mode TEXT,
  title TEXT,
  body TEXT,
  payload_json TEXT,
  status TEXT,
  sent_at TEXT,
  dedupe_key TEXT,
  retry_count INTEGER DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS notification_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id TEXT,
  mode TEXT,
  channel TEXT,
  created_at TEXT,
  window_start TEXT,
  window_end TEXT,
  status TEXT,
  summary_json TEXT,
  sent_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS notifications_dedupe_key_idx
ON notifications(dedupe_key)
WHERE dedupe_key IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS srun_spool_event_idx
ON job_events(note) WHERE event_type = 'SRUN_FINISHED' AND note IS NOT NULL;
CREATE INDEX IF NOT EXISTS work_ready_idx ON work_items(status, available_at, id);
CREATE INDEX IF NOT EXISTS work_job_idx ON work_items(job_id, id);
CREATE INDEX IF NOT EXISTS events_job_idx ON job_events(job_id, id);
CREATE INDEX IF NOT EXISTS commands_job_idx ON job_commands(job_id, id);
CREATE INDEX IF NOT EXISTS files_job_idx ON job_files(job_id, id);
CREATE INDEX IF NOT EXISTS analysis_job_idx ON job_analysis(job_id, id);
CREATE INDEX IF NOT EXISTS notifications_job_idx ON notifications(job_id, id);
CREATE INDEX IF NOT EXISTS notifications_ready_idx ON notifications(status, channel, mode, id);
"""


JOB_COLUMNS = [
    ("submitted_at", "TEXT"),
    ("submit_cwd", "TEXT"),
    ("effective_chdir", "TEXT"),
    ("command", "TEXT"),
    ("original_script_path", "TEXT"),
    ("copied_script_path", "TEXT"),
    ("user", "TEXT"),
    ("job_name", "TEXT"),
    ("partition", "TEXT"),
    ("account", "TEXT"),
    ("qos", "TEXT"),
    ("array_spec", "TEXT"),
    ("dependency", "TEXT"),
    ("stdout_path", "TEXT"),
    ("stderr_path", "TEXT"),
    ("git_commit", "TEXT"),
    ("git_dirty", "INTEGER"),
    ("state", "TEXT"),
    ("exit_code", "TEXT"),
    ("derived_exit_code", "TEXT"),
    ("reason", "TEXT"),
    ("elapsed", "TEXT"),
    ("max_rss", "TEXT"),
    ("nodes", "TEXT"),
    ("alloc_cpus", "INTEGER"),
    ("nodelist", "TEXT"),
    ("summary_json", "TEXT"),
    ("completion_summary_json", "TEXT"),
    ("tags", "TEXT"),
    ("created_at", "TEXT"),
    ("updated_at", "TEXT"),
]


DEFAULT_BUSY_TIMEOUT_MS = 30000
DEFAULT_LOCK_RETRY_SECONDS = 120
LOCKED_ERROR_MARKERS = (
    "database is locked",
    "database table is locked",
    "database is busy",
)
T = TypeVar("T")


def _busy_timeout_ms() -> int:
    value = os.environ.get("CSLURM_DB_BUSY_TIMEOUT_MS")
    if value is None:
        return DEFAULT_BUSY_TIMEOUT_MS
    return max(0, int(value))


def _lock_retry_seconds() -> float:
    value = os.environ.get("CSLURM_DB_LOCK_RETRY_SECONDS")
    if value is None:
        return DEFAULT_LOCK_RETRY_SECONDS
    return max(0.0, float(value))


def _is_locked_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in LOCKED_ERROR_MARKERS)


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path: Path | None = None) -> sqlite3.Connection:
    require_local_database(path)
    root_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path or db_path(), timeout=_busy_timeout_ms() / 1000, factory=Connection)
    try:
        conn.execute(f"pragma busy_timeout = {_busy_timeout_ms()}")
        # Avoid both cross-host WAL and the WAL-reset race in older SQLite builds.
        if conn.execute("pragma journal_mode").fetchone()[0].lower() == "wal":
            raise RuntimeError("Legacy WAL database: stop all clients, back it up and check integrity, "
                               "then run PRAGMA journal_mode=DELETE on its local management host.")
        conn.execute("pragma synchronous = full")
    except BaseException:
        conn.close()
        raise
    conn.row_factory = sqlite3.Row
    return conn


def run_write_transaction(
    write: Callable[[sqlite3.Connection], T],
    *,
    retry_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    deadline = time.monotonic() + (_lock_retry_seconds() if retry_seconds is None else retry_seconds)
    delay = 0.05
    while True:
        try:
            with connect() as conn:
                init_db(conn)
                result = write(conn)
                conn.commit()
                return result
        except sqlite3.OperationalError as exc:
            if not _is_locked_error(exc) or time.monotonic() >= deadline:
                raise
            sleep(delay)
            delay = min(delay * 2, 2.0)


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "jobs", JOB_COLUMNS)
    conn.execute("create index if not exists jobs_submitted_idx on jobs(submitted_at desc, job_id desc)")
    conn.execute("create index if not exists jobs_state_idx on jobs(state)")
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]) -> None:
    existing = {row[1] for row in conn.execute(f"pragma table_info({table})")}
    for name, declaration in columns:
        if name not in existing:
            conn.execute(f"alter table {table} add column {name} {declaration}")
