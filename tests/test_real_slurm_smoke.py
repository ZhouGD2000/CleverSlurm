import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cslurm.cli.cjobs import show_logs
from cslurm.slurm.tracker import TERMINAL_STATES, track_once
from real_slurm_support import first_available_partition, probe_real_slurm


pytestmark = pytest.mark.real_slurm


def _real_workdir() -> Path:
    base = Path(os.environ.get("CSLURM_REAL_SLURM_WORKDIR", "~/.cslurm-real-tests")).expanduser()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    workdir = base / f"pytest-{stamp}-{os.getpid()}"
    workdir.mkdir(parents=True, exist_ok=False)
    return workdir


def _submission_args() -> list[str]:
    args = [
        "--job-name",
        "cslurm-smoke",
        "--nodes=1",
        "--ntasks=1",
        "--cpus-per-task=1",
        "--time=00:02:00",
        "--output=smoke-%j.out",
        "--error=smoke-%j.err",
    ]
    partition = first_available_partition()
    if partition:
        args.extend(["--partition", partition])
    account = os.environ.get("CSLURM_REAL_SLURM_ACCOUNT")
    if account:
        args.extend(["--account", account])
    qos = os.environ.get("CSLURM_REAL_SLURM_QOS")
    if qos:
        args.extend(["--qos", qos])
    args.extend(["--wrap", "hostname; echo cleverslurm real smoke ok"])
    return args


def _job_state(root: Path, job_id: str) -> str | None:
    with sqlite3.connect(root / "db.sqlite") as conn:
        row = conn.execute("select state from jobs where job_id = ?", (job_id,)).fetchone()
    return row[0] if row else None


def test_real_slurm_commands_are_reachable():
    probe = probe_real_slurm()

    assert probe.available is True
    assert set(probe.commands) == {"sbatch", "sacct", "squeue", "sinfo", "srun", "scancel"}


def test_real_slurm_csbatch_smoke_job_completes(monkeypatch):
    from cslurm.cli.csbatch import submit_batch

    workdir = _real_workdir()
    root = workdir / ".cslurm"
    root.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("CSLURM_ROOT", str(root))
    monkeypatch.setenv("CSLURM_AI_AUTO_SUMMARY", "false")
    monkeypatch.setenv("CSLURM_STATIC_ANALYSIS", "false")
    monkeypatch.setenv("CSLURM_NOTIFICATION_ENABLED", "false")

    try:
        job_id = submit_batch(_submission_args())
    except RuntimeError as exc:
        pytest.skip(f"real Slurm submission failed: {exc}")

    deadline = time.monotonic() + int(os.environ.get("CSLURM_REAL_SLURM_TIMEOUT_SECONDS", "180"))
    state = _job_state(root, job_id)
    while time.monotonic() < deadline:
        track_once()
        state = _job_state(root, job_id)
        if state in TERMINAL_STATES:
            break
        time.sleep(5)
    else:
        pytest.skip(f"real Slurm smoke job {job_id} did not finish before timeout; state={state}")

    assert state == "COMPLETED"
    assert "cleverslurm real smoke ok" in show_logs(job_id, tail=20)
