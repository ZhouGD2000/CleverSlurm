# Native-first srun and scancel

Apply the already approved sbatch contract to the remaining wrappers: native
validation, unchanged argv, inherited CLI stdio/FDs, native return status, and
best-effort receipt publication outside SQLite. Keep existing files and historical
notifications; never execute a real cancellation during verification.

## Design

- Share executable resolution and process/signal utilities with sbatch. Do not
  change the child PATH when skipping managed or renamed Python entrypoints.
- srun/scancel CLI streams are inherited, not captured. Terminal Ctrl+C must not
  be delivered twice to srun: when in the terminal foreground group, the parent
  lets the terminal deliver SIGINT/SIGQUIT directly. Direct-exec opt-out remains
  available when exact process identity or targeted foreground signals matter.
- Both CLIs publish independent receipts. Management-host tracker/worker imports
  them idempotently. Step completion never replaces its allocation's state.
- No CleverSlurm CLI arguments are stripped. Use CSLURM_CANCEL_NOTE (or a Python
  keyword argument) for notes; update the web caller. Cancellation bookkeeping is
  an audit of requests, not proof of the scheduler's resulting state. Ambiguous
  filters, interactive declines, cluster selectors and step signals must not be
  labelled as confirmed whole-job cancellation.

## Execution Plan

1. Add failing fake tests for exact argv, bytes, failures, descriptors, locked or
   unwritable storage, managed/renamed commands, PTYs and Ctrl+C delivery.
2. Implement shared native utilities, receipts, conservative cancellation audit,
   and tracker/worker ingestion; adapt old tests to asynchronous bookkeeping.
3. Update web cancellation notes and documentation, review the diff and run the
   full local suite. Commit before deployment.
4. Preserve an additive mgmt code archive and fast-forward tracked source only.
   Run fake tests in isolation and real srun short-job/PTY tests. Real scancel
   tests are restricted to help, usage and version, with selector environment
   variables removed. No real cancellation or signal request is allowed.
5. Compare local/remote tracked-file hashes and verify production config, database
   and notification safety without replaying history.
