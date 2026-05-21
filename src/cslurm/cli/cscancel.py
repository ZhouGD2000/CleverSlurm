import os
import re
from datetime import datetime, timezone

from cslurm.db import connect, init_db
from cslurm.slurm.commands import run_slurm_command


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_note(argv: list[str]) -> tuple[list[str], str | None]:
    passthrough = []
    note = None
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--note":
            if index + 1 >= len(argv):
                raise SystemExit("cscancel --note requires an argument")
            note = argv[index + 1]
            index += 2
            continue
        if value.startswith("--note="):
            note = value.split("=", 1)[1]
            index += 1
            continue
        passthrough.append(value)
        index += 1
    return passthrough, note


def _infer_job_id(argv: list[str]) -> str | None:
    for value in argv:
        if re.match(r"^\d+(?:[_\.][\w-]+)?$", value):
            return value
    return None


def cancel_job(argv: list[str]):
    passthrough, note = _extract_note(argv)
    if not passthrough:
        raise SystemExit("usage: cscancel [scancel-options] job_id [--note NOTE]")

    result = run_slurm_command("scancel", passthrough)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    job_id = _infer_job_id(passthrough)
    command = "scancel " + " ".join(passthrough)
    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, command, cwd, note, raw_output)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                _now(),
                "CANCEL_REQUESTED",
                command,
                os.getcwd(),
                note,
                result.stdout + result.stderr,
            ),
        )
        conn.commit()
    return result


def main() -> None:
    result = cancel_job(os.sys.argv[1:])
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=os.sys.stderr)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
