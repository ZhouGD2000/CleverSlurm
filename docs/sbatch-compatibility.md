# Native sbatch Compatibility

## Execution contract

`csbatch` calls the installed native `sbatch` with the original argument vector.
It does not force `--parsable`, move or rewrite scripts, consume stdin itself,
translate `--wrap` to Bash, insert an EXIT trap, or add options for tracking.
Slurm's installed version and plugins decide which syntax is valid.

Stdout is forwarded as bytes while the command runs; stderr is inherited directly.
Native exit codes and signal termination are preserved. `--wait` returning a
nonzero job exit code is not treated as a failed submission when an ID was seen.
Inherited descriptors such as `--export-file=<fd>` are passed through. The native
executable is resolved past managed shims without changing the job's PATH.

Examples (these have the same native Slurm meaning):

```bash
csbatch --help
csbatch --version
csbatch --test-only --parsable -p CPU --wrap='exit 0'
csbatch -i input.dat --acctg-freq=task=30 job.slurm arg1 arg2
csbatch --wrap='python run.py' --partition=CPU --parsable
csbatch --array=1-10 job.slurm
csbatch --wait job.slurm
csbatch --quiet job.slurm
csbatch < job.slurm
job_id=$(csbatch --parsable job.slurm)
```

Use `cshim install` to affect PATH-based subprocess calls, not just an interactive
shell alias. Existing managed shims continue to work after an editable source
upgrade. See the README for installation and removal commands.

## Tracking is out of band

The CLI does not open SQLite. It writes a unique, mode-0600 JSON receipt under
`$CSLURM_ROOT/spool/sbatch/` (default `~/.cslurm/spool/sbatch/`), then tries to wake
the bounded worker. Workers and `ctrack` import receipts, committing the job,
submission event and enabled static/AI work together. Process diagnostics are in
`workers.log`; work state is visible through `cwork list`.

Keep `ctrack auto on` enabled for recovery when a worker cannot start. A new job
may take a short time to appear in `cjobs` or the web console. With `--wait`, the
receipt is published after the native command returns, not at job start.

- `ingested/`: successfully imported receipts, retained for audit/replay safety.
- `unresolved/`: no trustworthy Job ID or a cluster-qualified ID; no guessed job.
- `invalid/`: unreadable JSON receipts retained for inspection.
- Top-level `*.json`: pending ingestion, including database/queue failures.

Replaying an imported receipt does not reset job state, replace summaries or
duplicate its submission event. New receipts never resubmit jobs. A database,
snapshot or worker failure does not alter native stdout/stderr or exit status.
If even receipt publication fails (for example disk full or an unwritable root),
tracking may be lost; native submission still succeeds. This is deliberately
different from rejecting a Slurm job because the tracking database is unavailable.

## Metadata and transparency limits

Execution passthrough and complete metadata are separate guarantees:

- `--quiet` suppresses Job ID output. This version retains an unresolved receipt;
  it does **not** automatically identify the job by guessing from accounting.
- Cluster-qualified responses such as `12345;other-cluster` are forwarded intact,
  but retained unresolved because the current database keys jobs by Job ID alone.
- Stdin is inherited unchanged, so stdin scripts are not pre-read or archived.
  An observed ID is still recorded, but static analysis needs an available script.
- Unknown/plugin syntax and heterogeneous resource groups are passed through even
  when metadata extraction cannot describe them. Missing metadata is not a native
  command error. Array/node-specific log patterns may remain templates.
- Readable regular script files up to 1 MiB are captured before execution, without
  modifying them. An externally edited file can still race Slurm's own read.
  Git metadata is observed by the ingestion worker, possibly later than submission;
  it is not proof of an exact submission-time repository snapshot.
- To observe Job IDs, normal tracking pipes native stdout. A plugin that depends
  on stdout being a TTY, the exact process tree, or output-descriptor identity can
  use the direct-exec path below. Thus tested command compatibility is not a claim
  of indistinguishability for every possible site plugin or process inspection.

```bash
CSLURM_TRACK_SUBMISSIONS=false csbatch [native-sbatch-arguments...]
```

This bypass performs native exec with inherited stdin/stdout/stderr and creates
no tracking receipt. It also works through a managed `sbatch` shim. Prefer an
explicit absolute native path when diagnosing an unmanaged alias/shim recursion.

## Completion and notifications

No new runtime `PROGRAM_FINISHED` marker is injected. `ctrack` records completion
from `sacct`, including array-parent aggregation, and queues analysis only for new
terminal transitions. Previously recorded terminal states, notifications and
exclusions are preserved. Installing this change does not perform a historical
completion scan or replay notifications.

## Verification

`tests/test_sbatch_transparency.py` covers fake native argv, bytes, stdin, inherited
FDs, signals, live output, concurrent same-name scripts, locked databases and
idempotent receipts. `tests/test_real_sbatch_parity.py` compares informational and
invalid invocations with real Slurm, checks `--test-only`, and submits three tiny
isolated file/stdin/wrap jobs, including `--wait` with exit 7. No tests cancel jobs.

Reference: [SchedMD sbatch manual](https://slurm.schedmd.com/sbatch.html).
