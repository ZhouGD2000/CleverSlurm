# Testing

The normal test suite does not require Slurm. It creates fake `sbatch`, `srun`, `sacct`, and `scancel` executables in temporary directories and points the code at those commands through `PATH`.

## Local Tests

Run:

```bash
python3 -m pytest -q
python3 -m compileall -q ai_slurm
```

The tests cover:

- `aisbatch` job id parsing from `sbatch --parsable`
- original and instrumented script snapshots
- preservation of leading `#SBATCH` directives
- sbatch command-line option passthrough
- batch program-finished marker ingestion
- stdout/stderr path parsing
- git commit/status/diff capture
- fake `srun` execution through `aisrun`
- fake `scancel` event recording
- fake `sacct` tracker updates, including no-header `sacct -n` output
- runtime `commands.log` ingestion
- `aijobs` details, events, files, commands, and logs
- `aijobs ask` with a fake AI client over recent job facts
- Julia static `include(...)` parsing
- content-addressed file storage
- large-file metadata policy
- SiliconFlow request construction and error handling

## Real Slurm Smoke Test

Use a new working directory and a fresh `AI_SLURM_ROOT`. This keeps the smoke test isolated from any existing tracking database.

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
AI_SLURM_ROOT=$PWD/.ai-slurm-real PYTHONPATH=/path/to/cleverslurm \
  python3 -m ai_slurm.cli.aisbatch smoke_real.slurm

AI_SLURM_ROOT=$PWD/.ai-slurm-real PYTHONPATH=/path/to/cleverslurm \
  python3 - <<'PY'
from ai_slurm.slurm.tracker import track_once
track_once()
PY
```

Inspect the job:

```bash
AI_SLURM_ROOT=$PWD/.ai-slurm-real PYTHONPATH=/path/to/cleverslurm \
  python3 - <<'PY'
from ai_slurm.cli.aijobs import show_job, show_logs
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
- Use a fresh test directory and fresh `AI_SLURM_ROOT`.
