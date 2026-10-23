"""Native scancel invocation; audit is best-effort and never retries cancellation."""

import os
import sys
from datetime import datetime, timezone

from cslurm.slurm import native
from cslurm.slurm.cancel_options import audit
from cslurm.slurm.sbatch_options import command_text


def cancel_job(argv: list[str], *, note: str | None = None, capture_output: bool = True, defer: bool = False):
    timestamp = datetime.now(timezone.utc).isoformat()
    result = native.run("scancel", argv, capture_output=capture_output)
    try:
        from cslurm.runtime.cancellation import publish, ingest
        from cslurm.db import connect, init_db
        metadata = audit(argv, os.environ)
        if metadata.get("informational"):
            return result
        path = publish({
            "time": timestamp, "cwd": os.getcwd(), "command": command_text("scancel", argv),
            "argv": argv, "audit": metadata, "returncode": result.returncode,
            "note": note if note is not None else os.environ.get("CSLURM_CANCEL_NOTE"),
            "stdout": result.stdout or "", "stderr": result.stderr or "",
        })
        if defer:
            from cslurm.work import wake
            wake()
        else:
            with connect() as conn:
                init_db(conn)
                ingest(conn, paths=[path])
    except Exception:
        pass
    return result


def main() -> None:
    try:
        if os.environ.get("CSLURM_TRACK_COMMANDS", "true").lower() in {"false", "0", "off"}:
            native.exec_native("scancel", sys.argv[1:])
        result = cancel_job(sys.argv[1:], capture_output=False, defer=True)
    except OSError as exc:
        print(f"cscancel: {exc}", file=sys.stderr)
        raise SystemExit(127 if isinstance(exc, FileNotFoundError) else 126)
    native.exit_like_native(result.returncode)


if __name__ == "__main__":
    main()
