import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ai_slurm.collect.git import collect_git_metadata
from ai_slurm.config import root_dir
from ai_slurm.db import connect, init_db
from ai_slurm.slurm.commands import run_slurm_command


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_job_name(script_text: str) -> str | None:
    for line in script_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#SBATCH --job-name="):
            return stripped.split("=", 1)[1]
        if stripped.startswith("#SBATCH -J "):
            return stripped.split(None, 2)[2]
    return None


def _parse_sbatch_path(script_text: str, option: str) -> str | None:
    long_prefix = f"#SBATCH --{option}"
    short_prefix = "#SBATCH -o" if option == "output" else "#SBATCH -e"
    for line in script_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(long_prefix + "="):
            return stripped.split("=", 1)[1].strip()
        if stripped.startswith(long_prefix + " "):
            return stripped.split(None, 2)[2].strip()
        if stripped.startswith(short_prefix + " "):
            return stripped.split(None, 2)[2].strip()
    return None


def _resolve_sbatch_path(path_text: str | None, script_dir: Path, job_id: str) -> str | None:
    if not path_text:
        return None
    expanded = path_text.replace("%j", job_id).replace("%A", job_id)
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = script_dir / path
    return str(path.resolve())


def _instrument_script(original: str) -> str:
    prelude = """\
export AI_SLURM_JOB_ID="${SLURM_JOB_ID:-unknown}"
export AI_SLURM_SUBMIT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
export AI_SLURM_LOG_DIR="$HOME/.ai-slurm/jobs/${SLURM_JOB_ID}/runtime"
export PATH="$HOME/.ai-slurm/wrappers:$PATH"
mkdir -p "$AI_SLURM_LOG_DIR"
"""
    lines = original.splitlines()
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    while insert_at < len(lines) and lines[insert_at].strip().startswith("#SBATCH"):
        insert_at += 1
    return "\n".join([*lines[:insert_at], prelude, *lines[insert_at:]]) + "\n"


def submit_batch(argv: list[str]) -> str:
    parser = argparse.ArgumentParser(prog="aisbatch")
    parser.add_argument("script")
    args = parser.parse_args(argv)

    script = Path(args.script).resolve()
    script_text = script.read_text()
    submitted_at = _now()
    command = "aisbatch " + " ".join(argv)

    prepared_root = root_dir() / "pending"
    prepared_root.mkdir(parents=True, exist_ok=True)
    prepared_script = prepared_root / f"{script.stem}.instrumented.slurm"
    prepared_script.write_text(_instrument_script(script_text))

    result = run_slurm_command("sbatch", ["--parsable", str(prepared_script)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    job_id = result.stdout.strip().split(";", 1)[0]
    if not job_id:
        raise RuntimeError("sbatch did not return a job id")

    job_dir = root_dir() / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    copied_original = job_dir / "original.slurm"
    copied_instrumented = job_dir / "instrumented.slurm"
    shutil.copyfile(script, copied_original)
    shutil.copyfile(prepared_script, copied_instrumented)
    git_meta = collect_git_metadata(Path.cwd())
    (job_dir / "git_status.txt").write_text(git_meta.status)
    (job_dir / "git.diff").write_text(git_meta.diff)
    stdout_path = _resolve_sbatch_path(_parse_sbatch_path(script_text, "output"), script.parent, job_id)
    stderr_path = _resolve_sbatch_path(_parse_sbatch_path(script_text, "error"), script.parent, job_id)

    with connect() as conn:
        init_db(conn)
        conn.execute(
            """
            insert or replace into jobs (
              job_id, submitted_at, submit_cwd, command, original_script_path,
              copied_script_path, job_name, stdout_path, stderr_path, git_commit, git_dirty,
              state, created_at, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                submitted_at,
                os.getcwd(),
                command,
                str(script),
                str(copied_instrumented),
                _parse_job_name(script_text),
                stdout_path,
                stderr_path,
                git_meta.commit,
                1 if git_meta.dirty else 0,
                "UNKNOWN",
                submitted_at,
                submitted_at,
            ),
        )
        conn.execute(
            """
            insert into job_events (job_id, event_time, event_type, command, cwd, raw_output)
            values (?, ?, ?, ?, ?, ?)
            """,
            (job_id, submitted_at, "SUBMITTED", command, os.getcwd(), result.stdout),
        )
        conn.commit()

    return job_id


def main() -> None:
    print(submit_batch(os.sys.argv[1:]))


if __name__ == "__main__":
    main()
