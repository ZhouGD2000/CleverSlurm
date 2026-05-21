import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cslurm.collect.git import collect_git_metadata
from cslurm.ai.auto import record_auto_summary_failure, record_auto_summary_queued
from cslurm.config import ai_auto_summary_enabled, root_dir
from cslurm.db import connect, init_db
from cslurm.slurm.commands import run_slurm_command


SBATCH_LONG_OPTIONS_WITH_ARG = {
    "account",
    "array",
    "begin",
    "chdir",
    "cluster-constraint",
    "clusters",
    "comment",
    "constraint",
    "container",
    "container-id",
    "cores-per-socket",
    "cpu-bind",
    "cpus-per-gpu",
    "cpus-per-task",
    "dependency",
    "distribution",
    "error",
    "exclude",
    "export",
    "export-file",
    "extra-node-info",
    "gid",
    "gpus",
    "gpus-per-node",
    "gpus-per-socket",
    "gpus-per-task",
    "gres",
    "gres-flags",
    "hint",
    "input",
    "job-name",
    "licenses",
    "mail-type",
    "mail-user",
    "mem",
    "mem-bind",
    "mem-per-cpu",
    "mem-per-gpu",
    "mincpus",
    "nodes",
    "nodelist",
    "ntasks",
    "ntasks-per-core",
    "ntasks-per-gpu",
    "ntasks-per-node",
    "ntasks-per-socket",
    "open-mode",
    "output",
    "partition",
    "qos",
    "reservation",
    "signal",
    "sockets-per-node",
    "threads-per-core",
    "time",
    "time-min",
    "uid",
    "wckey",
    "wrap",
}

SBATCH_SHORT_OPTIONS_WITH_ARG = set("AacCDeEJLNnopqtuwx")


@dataclass(frozen=True)
class ParsedSbatchInvocation:
    passthrough_args: list[str]
    script_args: list[str]
    script_path: Path | None
    wrap_command: str | None
    user_parsable: bool


@dataclass(frozen=True)
class BatchSubmission:
    job_id: str
    user_parsable: bool
    stderr: str


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


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _parse_sbatch_invocation(argv: list[str]) -> ParsedSbatchInvocation:
    passthrough: list[str] = []
    user_parsable = False
    index = 0
    while index < len(argv):
        value = argv[index]
        if value == "--":
            if index + 1 >= len(argv):
                raise SystemExit("usage: csbatch [sbatch-options] script [script-args]")
            return ParsedSbatchInvocation(passthrough, argv[index + 2 :], Path(argv[index + 1]).resolve(), None, user_parsable)
        if value == "--parsable":
            user_parsable = True
            index += 1
            continue
        if value == "--wrap":
            if index + 1 >= len(argv):
                raise SystemExit("csbatch --wrap requires a command")
            return ParsedSbatchInvocation(passthrough, [], None, argv[index + 1], user_parsable)
        if value.startswith("--wrap="):
            return ParsedSbatchInvocation(passthrough, [], None, value.split("=", 1)[1], user_parsable)
        if value.startswith("--"):
            passthrough.append(value)
            name = value[2:].split("=", 1)[0]
            if "=" not in value and name in SBATCH_LONG_OPTIONS_WITH_ARG:
                if index + 1 >= len(argv):
                    raise SystemExit(f"csbatch {value} requires an argument")
                passthrough.append(argv[index + 1])
                index += 2
                continue
            index += 1
            continue
        if value.startswith("-") and value != "-":
            passthrough.append(value)
            if len(value) == 2 and value[1] in SBATCH_SHORT_OPTIONS_WITH_ARG:
                if index + 1 >= len(argv):
                    raise SystemExit(f"csbatch {value} requires an argument")
                passthrough.append(argv[index + 1])
                index += 2
                continue
            index += 1
            continue
        script_path = Path(value).resolve()
        if not script_path.exists() or not script_path.is_file():
            raise SystemExit(f"csbatch script does not exist or is not a file: {value}")
        return ParsedSbatchInvocation(passthrough, argv[index + 1 :], script_path, None, user_parsable)
    raise SystemExit("usage: csbatch [sbatch-options] script [script-args]")


def _instrument_script(original: str, cslurm_root: Path) -> str:
    root = str(cslurm_root.resolve())
    prelude = f"""\
export CSLURM_ROOT={_shell_single_quote(root)}
export CSLURM_JOB_ID="${{SLURM_JOB_ID:-unknown}}"
export CSLURM_SUBMIT_DIR="${{SLURM_SUBMIT_DIR:-$(pwd)}}"
export CSLURM_LOG_DIR="$CSLURM_ROOT/jobs/$CSLURM_JOB_ID/runtime"
export PATH="$CSLURM_ROOT/wrappers:$PATH"
mkdir -p "$CSLURM_LOG_DIR"
_cslurm_record_finish() {{
  _cslurm_exit_code=$?
  _cslurm_time=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  _cslurm_host=$(hostname 2>/dev/null || printf unknown)
  mkdir -p "$CSLURM_LOG_DIR"
  printf '{{"time":"%s","job_id":"%s","hostname":"%s","exit_code":%s,"event_type":"PROGRAM_FINISHED"}}\\n' "$_cslurm_time" "$CSLURM_JOB_ID" "$_cslurm_host" "$_cslurm_exit_code" >> "$CSLURM_LOG_DIR/finish.log"
}}
trap _cslurm_record_finish EXIT
"""
    lines = original.splitlines()
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
    while insert_at < len(lines) and lines[insert_at].strip().startswith("#SBATCH"):
        insert_at += 1
    return "\n".join([*lines[:insert_at], prelude, *lines[insert_at:]]) + "\n"


def _wrap_script_text(command: str) -> str:
    return "#!/bin/bash\n" + command + "\n"


def launch_auto_summary(job_id: str) -> str:
    if not ai_auto_summary_enabled():
        return "disabled"
    job_dir = root_dir() / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path = job_dir / "auto_summary.log"
    try:
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [sys.executable, "-m", "cslurm.ai.auto", job_id],
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
            )
    except Exception as exc:
        record_auto_summary_failure(job_id, exc)
        return "failed"
    record_auto_summary_queued(job_id, pid=process.pid)
    return "queued"


def submit_batch_result(argv: list[str]) -> BatchSubmission:
    parsed = _parse_sbatch_invocation(argv)
    script = parsed.script_path
    script_text = script.read_text() if script is not None else _wrap_script_text(parsed.wrap_command or "")
    submitted_at = _now()
    command = "csbatch " + " ".join(argv)

    prepared_root = root_dir() / "pending"
    prepared_root.mkdir(parents=True, exist_ok=True)
    prepared_stem = script.stem if script is not None else "wrap"
    prepared_script = prepared_root / f"{prepared_stem}.instrumented.slurm"
    prepared_script.write_text(_instrument_script(script_text, root_dir()))

    result = run_slurm_command("sbatch", ["--parsable", *parsed.passthrough_args, str(prepared_script), *parsed.script_args])
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    job_id = result.stdout.strip().split(";", 1)[0]
    if not job_id:
        raise RuntimeError("sbatch did not return a job id")

    job_dir = root_dir() / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    copied_original = job_dir / "original.slurm"
    copied_instrumented = job_dir / "instrumented.slurm"
    if script is not None:
        shutil.copyfile(script, copied_original)
    else:
        copied_original.write_text(script_text)
    shutil.copyfile(prepared_script, copied_instrumented)
    git_meta = collect_git_metadata(Path.cwd())
    (job_dir / "git_status.txt").write_text(git_meta.status)
    (job_dir / "git.diff").write_text(git_meta.diff)
    script_dir = script.parent if script is not None else Path.cwd()
    stdout_path = _resolve_sbatch_path(_parse_sbatch_path(script_text, "output"), script_dir, job_id)
    stderr_path = _resolve_sbatch_path(_parse_sbatch_path(script_text, "error"), script_dir, job_id)

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
                str(script) if script is not None else None,
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

    launch_auto_summary(job_id)
    return BatchSubmission(job_id=job_id, user_parsable=parsed.user_parsable, stderr=result.stderr)


def submit_batch(argv: list[str]) -> str:
    return submit_batch_result(argv).job_id


def main() -> None:
    result = submit_batch_result(os.sys.argv[1:])
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.user_parsable:
        print(result.job_id)
    else:
        print(f"Submitted batch job {result.job_id}")


if __name__ == "__main__":
    main()
