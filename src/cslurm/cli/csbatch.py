"""sbatch passthrough with best-effort, out-of-band submission tracking."""

import getpass
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cslurm.slurm import native_sbatch
from cslurm.slurm.sbatch_options import (
    SBATCH_LONG_OPTIONS_WITH_ARG, SBATCH_SHORT_OPTIONS_WITH_ARG,
    parse_cli_option, parse_script_option,
)


@dataclass(frozen=True)
class BatchSubmission:
    job_id: str | None
    stdout: str
    stderr: str
    returncode: int


def _metadata_args(argv):
    """Never validate execution here; unknown syntax only limits metadata."""
    index = 0
    options = []
    while index < len(argv):
        value = argv[index]
        if value == "--":
            return options, argv[index + 1] if index + 1 < len(argv) else None
        if value == ":":
            # A heterogeneous submission is not one resource description.
            return [], None
        if not value.startswith("-") or value == "-":
            return options, value
        if value.startswith("--"):
            name = value[2:].split("=", 1)[0]
            if "=" not in value and name in SBATCH_LONG_OPTIONS_WITH_ARG:
                if index + 1 >= len(argv):
                    return options, None
                options.append(value + "=" + argv[index + 1])
                index += 2
            elif "=" in value or name in {
                "parsable", "quiet", "verbose", "wait", "test-only", "help",
                "usage", "version", "hold", "requeue", "no-requeue", "exclusive",
                "oversubscribe", "overcommit", "get-user-env", "contiguous",
                "ignore-pbs", "no-kill", "spread-job", "use-min-nodes",
            }:
                options.append(value)
                index += 1
            else:
                return [], None
        else:
            consumed = False
            for offset, char in enumerate(value[1:], 1):
                if char in SBATCH_SHORT_OPTIONS_WITH_ARG:
                    argument = value[offset + 1:]
                    if not argument and index + 1 < len(argv):
                        argument = argv[index + 1]
                    options.append("-" + char + argument)
                    index += 2 if offset == len(value) - 1 else 1
                    consumed = True
                    break
                if char not in "vQWHhVOsk":
                    return [], None
                options.append("-" + char)
            if not consumed:
                index += 1
    return options, None


def _snapshot(argv):
    cwd = Path.cwd()
    options, script_arg = _metadata_args(argv)
    wrap = parse_cli_option(options, "wrap")
    script, script_text = None, None
    chdir = parse_cli_option(options, "chdir", "-D") or os.environ.get("SBATCH_CHDIR")
    working = Path(chdir) if chdir else cwd
    if not working.is_absolute():
        working = cwd / working
    if wrap is not None:
        # Archive a representation only. Slurm still receives the original --wrap.
        script_text = "#!/bin/sh\n" + wrap + "\n"
    elif script_arg and script_arg != "-":
        candidates = [working / script_arg, cwd / script_arg]
        found = shutil.which(script_arg)
        if found:
            candidates.append(Path(found))
        script = next((p.resolve() for p in candidates if p.is_file()), None)
        if script is not None and script.stat().st_size <= 1024 * 1024:
            script_text = script.read_bytes().decode(errors="surrogateescape")
    metadata = {}
    for name, short, env in (
        ("job-name", "-J", "SBATCH_JOB_NAME"), ("partition", "-p", "SBATCH_PARTITION"),
        ("account", "-A", "SBATCH_ACCOUNT"), ("qos", "-q", "SBATCH_QOS"),
        ("array", "-a", "SBATCH_ARRAY_INX"), ("dependency", "-d", "SBATCH_DEPENDENCY"),
        ("nodes", "-N", "SBATCH_NODES"), ("nodelist", "-w", "SBATCH_NODELIST"),
        ("output", "-o", "SBATCH_OUTPUT"), ("error", "-e", "SBATCH_ERROR"),
        ("chdir", "-D", "SBATCH_CHDIR"),
    ):
        metadata[name] = (parse_cli_option(options, name, short) or os.environ.get(env)
                          or parse_script_option(script_text or "", name, short))
    if not metadata["job-name"]:
        metadata["job-name"] = script.name if script else ("wrap" if wrap is not None else None)
    return {
        "argv": argv, "cwd": str(cwd), "time": datetime.now(timezone.utc).isoformat(),
        "script_path": str(script) if script else None, "script_text": script_text,
        "metadata": metadata, "user": os.environ.get("USER") or getpass.getuser(),
        "no_submission": any(v in {"--help", "--usage", "--version", "--test-only", "-h", "-V"}
                             for v in options),
    }


def _job_identity(output):
    for line in output.splitlines():
        match = re.fullmatch(
            r"(?:Submitted batch job )?([0-9]+)(?:;([\w.-]+)| on cluster ([\w.-]+))?", line.strip())
        if match:
            return match[1], match[2] or match[3]
    return None, None


def submit_batch_result(argv: list[str], *, stream: bool = False, defer: bool = False) -> BatchSubmission:
    record = None
    try:
        record = _snapshot(argv)
    except Exception:
        # Metadata can never veto an invocation supported by the installed Slurm.
        pass
    result = native_sbatch.run(argv, stream=stream)
    stdout = result.stdout.decode(errors="replace")
    stderr = result.stderr.decode(errors="replace")
    job_id, cluster = _job_identity(stdout)
    if record is not None and record.get("no_submission"):
        job_id = None
    try:
        from cslurm.slurm.submissions import publish, ingest
        if record is None:
            record = {"argv": argv, "cwd": os.getcwd(),
                      "time": datetime.now(timezone.utc).isoformat(), "metadata": {}}
        record.update(job_id=job_id, cluster=cluster, stdout=stdout, returncode=result.returncode)
        if not record.get("no_submission"):
            path = publish(record)
            if defer:
                from cslurm.work import wake
                wake()
            else:
                ingest(paths=[path])
    except Exception:
        # Do not pollute native stderr or encourage a retry of an accepted job.
        # A published receipt remains available to the next tracker pass.
        pass
    return BatchSubmission(job_id, stdout, stderr, result.returncode)


def submit_batch(argv: list[str]) -> str | None:
    result = submit_batch_result(argv)
    if result.returncode and result.job_id is None:
        raise RuntimeError(result.stderr or result.stdout or f"sbatch exited {result.returncode}")
    return result.job_id


def main() -> None:
    try:
        if os.environ.get("CSLURM_TRACK_SUBMISSIONS", "true").lower() in {"false", "0", "off"}:
            executable = native_sbatch.executable()
            os.execv(executable, [executable, *sys.argv[1:]])
        result = submit_batch_result(sys.argv[1:], stream=True, defer=True)
    except OSError as exc:
        print(f"csbatch: {exc}", file=sys.stderr)
        raise SystemExit(127 if isinstance(exc, FileNotFoundError) else 126)
    native_sbatch.exit_like_native(result.returncode)


if __name__ == "__main__":
    main()
