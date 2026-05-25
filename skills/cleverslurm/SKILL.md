---
name: cleverslurm
description: Use when working with CleverSlurm, Slurm job tracking, csbatch/csrun/cscancel/cjobs/ctrack/cshim, fake or real Slurm tests, AI job summaries, or Feishu/Lark notifications for Slurm jobs.
---

# CleverSlurm

CleverSlurm is a Python CLI package for deterministic Slurm job tracking. The skill is only an agent guide; the durable tracking, SQLite database, Slurm wrappers, AI summaries, and notifications live in the Python package.

Repository: `git@github.com:ZhouGD2000/CleverSlurm.git`

## Start Here

1. Locate the CleverSlurm checkout or clone it from the repository above.
2. Prefer the repository's `README.md` for user-facing install commands.
3. Use `csbatch`, `csrun`, `cscancel`, `cjobs`, `ctrack`, `csummarize`, `cnotify`, and `cshim` as installed CLI commands when available.
4. For source checkouts without installation, use `PYTHONPATH=src python3 -m cslurm.cli.<command>`.
5. Never run broad cancellation commands. Do not run batch `scancel`; only cancel an explicit job id when the user asked for it.

## Common Commands

- `cjobs recent`: sacct-like recorded job history.
- `cjobs queue`: squeue-like active tracked jobs.
- `cjobs show JOBID`: stored metadata.
- `cjobs logs JOBID --tail 100`: recorded or inferred stdout/stderr.
- `cjobs summary JOBID`: stored submission AI summary.
- `cjobs summary JOBID --completion`: stored completion AI summary.
- `cjobs ask "最近完成了什么任务？"`: natural-language answer over recorded facts.
- `ctrack`: poll `sacct`, update tracked states, ingest runtime finish markers, and dispatch due notifications.

## Safety Rules

- Do not delete pre-existing cluster files or directories unless the user explicitly asks.
- Do not call `scancel -u`, wildcard cancellation, or any broad cancellation command.
- Real Slurm tests may submit one short smoke job; they should not cancel existing jobs.
- Prefer isolated `CSLURM_ROOT` values for tests and smoke runs.
- Do not commit API keys, Feishu secrets, personal absolute paths, or generated runtime databases.

## References

Read only what is needed:

- `references/usage.md`: install, CLI use, and user workflows.
- `references/development.md`: repo layout, tests, fake/real Slurm split, CI guidance.
- `references/cluster_safety.md`: shared-cluster guardrails.
- `references/ai_notifications.md`: AI provider config and Feishu notification behavior.
