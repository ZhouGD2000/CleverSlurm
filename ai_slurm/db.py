import sqlite3
from pathlib import Path

from ai_slurm.config import db_path, root_dir


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  submitted_at TEXT,
  submit_cwd TEXT,
  effective_chdir TEXT,
  command TEXT,
  original_script_path TEXT,
  copied_script_path TEXT,
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
  reason TEXT,
  elapsed TEXT,
  max_rss TEXT,
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
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    root_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
