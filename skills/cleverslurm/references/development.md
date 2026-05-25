# CleverSlurm Development

## Layout

- `src/cslurm/`: Python package.
- `src/cslurm/cli/`: console entrypoints.
- `src/cslurm/slurm/`: Slurm command helpers and tracker.
- `src/cslurm/ai/`: OpenAI-compatible and Anthropic-compatible AI clients and summaries.
- `src/cslurm/notify/`: deterministic analysis and Feishu/Lark dispatch.
- `src/cslurm/collect/static/`: static MATLAB, Python, Julia, and shell command recognition.
- `tests/`: pytest suite.
- `docs/`: user and implementation documentation.
- `skills/cleverslurm/`: Codex skill wrapper.

## Local Verification

Run fake/default tests:

```bash
python3 -m pytest -q -m "not real_slurm"
python3 -m compileall -q src/cslurm
python3 -m pytest tests/test_no_personal_paths.py -q
git diff --check
```

Run all tests. On hosts without Slurm, `real_slurm` tests should skip:

```bash
python3 -m pytest -q -rs
```

Run only real Slurm tests:

```bash
python3 -m pytest -q -m real_slurm -rs
```

Disable real tests even when Slurm exists:

```bash
CSLURM_RUN_REAL_SLURM=0 python3 -m pytest -q
```

## Real Slurm Tests

Real tests probe `sbatch`, `sacct`, `squeue`, `sinfo`, `srun`, and `scancel`. If available, they submit one short smoke job and wait for completion. They should not call `scancel`.

Optional environment:

```bash
export CSLURM_REAL_SLURM_WORKDIR=/path/on/shared/filesystem/cleverslurm-real-tests
export CSLURM_REAL_SLURM_PARTITION=CPU2
export CSLURM_REAL_SLURM_ACCOUNT=my-account
export CSLURM_REAL_SLURM_QOS=normal
export CSLURM_REAL_SLURM_TIMEOUT_SECONDS=180
```

## Git Hygiene

- Use git for changes.
- Do not revert user changes.
- Do not commit secrets or personal absolute paths.
- Do not delete old remote files unless explicitly asked.
- When syncing to a remote development host, confirm the repository path from the current conversation or user-provided context.

## CI Guidance

Use two CI layers:

- Normal GitHub-hosted runner: install the package and run fake tests with `-m "not real_slurm"`.
- Self-hosted Slurm runner: run `-m real_slurm` on a trusted branch or manual workflow.

Avoid running real cluster tests on untrusted fork PRs.
