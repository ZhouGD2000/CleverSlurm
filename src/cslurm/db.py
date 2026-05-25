import sqlite3
from pathlib import Path

from cslurm.config import db_path, root_dir


SCHEMA = """
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


def connect(path: Path | None = None) -> sqlite3.Connection:
    root_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_columns(conn, "jobs", JOB_COLUMNS)
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]) -> None:
    existing = {row[1] for row in conn.execute(f"pragma table_info({table})")}
    for name, declaration in columns:
        if name not in existing:
            conn.execute(f"alter table {table} add column {name} {declaration}")
