"""Best-effort metadata only. Never record prompts, response bodies or credentials."""

import sqlite3
from datetime import datetime, timezone

from cslurm.config import db_path
from cslurm.storage import require_local_database


def record_call(format, requested, response, elapsed_ms, error_code, fallback):
    try:
        path = db_path()
        if not path.exists():
            return
        require_local_database()
        conn = sqlite3.connect(path, timeout=0.05)
        try:
            conn.execute('insert into model_calls(created_at,format,requested_model,response_model,elapsed_ms,status,error_code,fallback) '
                         'values (?,?,?,?,?,?,?,?)',
                         (datetime.now(timezone.utc).isoformat(), format, requested, response, elapsed_ms,
                          'failed' if error_code else 'succeeded', error_code, int(fallback)))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Telemetry failure must never turn a successful model response into a failure.
        pass
