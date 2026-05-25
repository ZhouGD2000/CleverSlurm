# Testing

The test suite is split automatically:

- Fake Slurm tests always run. They create fake `sbatch`, `srun`, `sacct`, and `scancel` executables in temporary directories and put those directories first in `PATH`.
- Real Slurm tests are marked `real_slurm`. At collection time pytest probes `sbatch`, `sacct`, `squeue`, `sinfo`, `srun`, and `scancel`. If any command is unavailable or does not answer `--version`, the real tests are skipped. If all commands are available, pytest runs the real smoke tests.

Real Slurm tests may submit one short smoke job. They never call `scancel`.

## Local Tests

Run:

```bash
python3 -m pytest -q
python3 -m compileall -q src/cslurm
```

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
- original and instrumented script snapshots
- preservation of leading `#SBATCH` directives
- sbatch command-line option passthrough
- basic `sbatch --wrap` translation
- batch program-finished marker ingestion
- stdout/stderr path parsing
- background static MATLAB/Python submission analysis
- git commit/status/diff capture
- fake `srun` execution through `csrun`
- `csrun` CLI stdout/stderr and exit-code forwarding
- fake `scancel` event recording
- scancel option passthrough with CleverSlurm-only `--note`
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

For smoke tests on a shared cluster:

- Submit only a new short job that you own.
- Do not call `scancel` unless the specific smoke job is stuck and the owner explicitly approves it.
- Never run broad cancellation commands such as `scancel -u USER`.
- Do not delete pre-existing files or directories on the remote host.
- Use a fresh test directory and fresh `CSLURM_ROOT`.
