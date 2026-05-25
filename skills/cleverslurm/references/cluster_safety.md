# CleverSlurm Cluster Safety

## Hard Rules

- Never run broad cancellation commands such as `scancel -u USER`, wildcard job cancellation, or scripted cancellation over many job ids.
- Do not cancel pre-existing jobs unless the user explicitly names the job id and asks for cancellation.
- Do not delete pre-existing files or directories on shared cluster hosts unless explicitly asked.
- Do not overwrite user Slurm scripts when testing. Use fresh smoke directories and fresh `CSLURM_ROOT` values.
- Do not commit `~/.cslurm`, runtime databases, copied job scripts, API keys, webhook secrets, or personal absolute paths.

## Safe Smoke Pattern

Use a new directory and isolated root:

```bash
mkdir -p ~/cleverslurm-smoke
cd ~/cleverslurm-smoke
CSLURM_ROOT=$PWD/.cslurm-real PYTHONPATH=/path/to/CleverSlurm/src \
  python3 -m cslurm.cli.csbatch \
    --job-name cleverslurm-smoke \
    --time=00:02:00 \
    --nodes=1 \
    --ntasks=1 \
    --cpus-per-task=1 \
    --output=smoke-%j.out \
    --error=smoke-%j.err \
    --wrap "hostname; echo cleverslurm real smoke ok"
```

Then run `ctrack` or `python3 -m pytest -q -m real_slurm -rs`.

## Existing Workflows

If scripts call `sbatch`, `srun`, or `scancel` directly, use `cshim install` instead of relying on aliases. Aliases usually do not apply to non-interactive SSH commands or Python subprocess calls.
