# Testing

The normal test suite does not require Slurm. It creates fake `sbatch`, `srun`, `sacct`, and `scancel` executables in temporary directories and points the code at those commands through `PATH`.

## Local Tests

Run:

```bash
python3 -m pytest -q
python3 -m compileall -q src/cslurm
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

Use a new working directory and a fresh `CSLURM_ROOT`. This keeps the smoke test isolated from any existing tracking database.

```bash
mkdir -p ~/cleverslurm-smoke
cd ~/cleverslurm-smoke
```

Create a tiny job:

```bash
cat > smoke_real.slurm <<'EOF'
#!/bin/bash
#SBATCH --job-name=cleverslurm-smoke
#SBATCH --partition=CPU2
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --time=00:01:00
#SBATCH --output=smoke-%j.out
#SBATCH --error=smoke-%j.err
hostname
python3 -c "print('cleverslurm smoke ok')"
EOF
```

Submit and track:

```bash
CSLURM_ROOT=$PWD/.cslurm-real PYTHONPATH=/path/to/cleverslurm/src \
  python3 -m cslurm.cli.csbatch smoke_real.slurm

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
cleverslurm smoke ok
```

## Cluster Safety

For smoke tests on a shared cluster:

- Submit only a new short job that you own.
- Do not call `scancel` unless the specific smoke job is stuck and the owner explicitly approves it.
- Never run broad cancellation commands such as `scancel -u USER`.
- Do not delete pre-existing files or directories on the remote host.
- Use a fresh test directory and fresh `CSLURM_ROOT`.
