# Testing

## GitHub Actions

`.github/workflows/ci.yml` runs fake/unit tests on Python 3.10, 3.12 and 3.13.
A separate disposable Ubuntu runner installs Munge, MariaDB, `slurmdbd`,
`slurmctld` and `slurmd`. `ci/setup_slurm.py` configures a single-node `debug`
partition with real accounting, then runs the `real_slurm` marker.
`CSLURM_REQUIRE_REAL_SLURM=true` makes missing daemons, rejected submissions or
completion timeouts fail CI instead of silently skipping the integration tests.

The setup script is restricted to root on GitHub Actions. Never run it on an
existing cluster: it is for a disposable runner, not production installation.
No API keys or Feishu credentials are required. Real tests submit their own
short-lived job into an isolated data root and never cancel existing jobs.
Ordinary local runs keep automatic environment detection and skip real tests
when a usable scheduler is absent; fake coverage runs regardless.

Reliability regressions also cover notification claim races and interrupted
sends, network calls outside SQLite transactions, durable work recovery,
historical notification exclusion, protected model fields, metadata redaction,
non-overwriting backups, combined filters and authenticated health endpoints.

The test suite is split automatically:

- Fake Slurm tests always run. They create fake `sbatch`, `srun`, `sacct`, and `scancel` executables in temporary directories and put those directories first in `PATH`.
- Real Slurm tests are marked `real_slurm`. At collection time pytest probes `sbatch`, `sacct`, `squeue`, `sinfo`, `srun`, and `scancel`. If any command is unavailable or does not answer `--version`, the real tests are skipped. If all commands are available, pytest runs the real smoke tests.

Real Slurm tests submit one short smoke job plus three short file/stdin/wrap
parity jobs. They use isolated roots, disable notifications, and never call
`scancel`. Informational, invalid-option and `--test-only` comparisons create no jobs.

## Local Tests

Run:

```bash
python3 -m pip install -e . pytest setuptools wheel
python3 -m pytest -q
python3 -m compileall -q src/cslurm
```

The offline remote-installer tests build a wheel with `--no-build-isolation`.
Install `setuptools` and `wheel` in the test interpreter itself: the isolated
build dependencies used by `pip install -e .` do not remain in that environment.
In particular, a fresh Python 3.12+ virtual environment does not supply these
packaging tools automatically. The GitHub Actions unit-test matrix installs them
explicitly as well.

Run only fake Slurm tests:

```bash
python3 -m pytest -q -m "not real_slurm"
```

Run only real Slurm tests and show skip reasons:

```bash
python3 -m pytest -q -m real_slurm -rs
```

Disable real Slurm tests even on a host where Slurm is installed:

```bash
CSLURM_RUN_REAL_SLURM=0 python3 -m pytest -q
```

The tests cover:

- `csbatch` job id parsing from `sbatch --parsable`
- unchanged original script snapshots, including non-shell scripts
- native handling of `#SBATCH` directives after blank/comment lines
- exact sbatch argument, stdin, descriptor, output-byte and exit-status passthrough
- native `sbatch --wrap`, help, version, test-only, quiet and wait behavior
- legacy batch program-finished marker ingestion (no new trap is injected)
- durable submission receipts, database-lock isolation and idempotent ingestion
- stdout/stderr path parsing
- background static MATLAB/Python submission analysis
- git commit/status/diff capture
- fake `srun` execution through `csrun`
- `csrun` CLI stdout/stderr and exit-code forwarding
- fake `scancel` event recording
- unchanged scancel arguments, interactive prompts, and optional `CSLURM_CANCEL_NOTE`
- fake `sacct` tracker updates, including no-header `sacct -n` output
- optional runtime `commands.log` ingestion
- `cjobs` details, events, files, commands, and logs
- `cjobs ask` with a fake AI client over recent job facts
- Julia static `include(...)` parsing
- content-addressed file storage
- large-file metadata policy
- OpenAI-compatible and Anthropic-compatible model request construction and error handling
- notification classification, queue deduplication, and Feishu immediate/grouped webhook dispatch with fake HTTP

## Real Slurm Smoke Test

The automated real Slurm pytest writes into a fresh directory under `~/.cslurm-real-tests/` by default and uses a fresh `CSLURM_ROOT` inside that directory. Override the base directory when needed:

```bash
export CSLURM_REAL_SLURM_WORKDIR=/path/on/shared/filesystem/cleverslurm-real-tests
```

Optional cluster-specific settings:

```bash
export CSLURM_REAL_SLURM_PARTITION=CPU2
export CSLURM_REAL_SLURM_ACCOUNT=my-account
export CSLURM_REAL_SLURM_QOS=normal
export CSLURM_REAL_SLURM_TIMEOUT_SECONDS=180
```

If `CSLURM_REAL_SLURM_PARTITION` is not set, the test asks `sinfo` for the first available partition and lets `sbatch` use the cluster default if no partition can be detected.

Manual smoke tests are still useful when you want to inspect every command. Use a new working directory and a fresh `CSLURM_ROOT`:

```bash
mkdir -p ~/cleverslurm-smoke
cd ~/cleverslurm-smoke
```

Submit a tiny wrapped job:

```bash
CSLURM_ROOT=$PWD/.cslurm-real PYTHONPATH=/path/to/cleverslurm/src \
  python3 -m cslurm.cli.csbatch \
    --job-name cleverslurm-smoke \
    --time=00:02:00 \
    --nodes=1 \
    --ntasks=1 \
    --cpus-per-task=1 \
    --output=smoke-%j.out \
    --error=smoke-%j.err \
    --wrap "hostname; echo cleverslurm real smoke ok"

CSLURM_ROOT=$PWD/.cslurm-real PYTHONPATH=/path/to/cleverslurm/src \
  python3 - <<'PY'
from cslurm.slurm.tracker import track_once
track_once()
PY
```

Inspect the job:

```bash
CSLURM_ROOT=$PWD/.cslurm-real PYTHONPATH=/path/to/cleverslurm/src \
  python3 - <<'PY'
from cslurm.cli.cjobs import show_job, show_logs
job_id = "replace-with-job-id"
print(show_job(job_id))
print(show_logs(job_id, tail=50))
PY
```

Expected result:

```text
state: COMPLETED
exit_code: 0:0
cleverslurm real smoke ok
```

## Cluster Safety

For web model configuration, see the [catalog and saved-selection verification](testing-model-catalog.md)
procedure and live provider results. This test needs no Slurm submissions.

For smoke tests on a shared cluster:

- Submit only a new short job that you own.
- Do not call `scancel` unless the specific smoke job is stuck and the owner explicitly approves it.
- Never run broad cancellation commands such as `scancel -u USER`.
- Do not delete pre-existing files or directories on the remote host.
- Use a fresh test directory and fresh `CSLURM_ROOT`.
