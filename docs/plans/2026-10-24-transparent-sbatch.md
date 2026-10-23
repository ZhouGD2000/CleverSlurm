# Transparent sbatch Execution

## Approved Design

Native Slurm owns argument validation, script resolution, stdin, shell selection,
and submission. CleverSlurm must not inject options, replace scripts, reinterpret
`--wrap`, regenerate output, or convert tracking errors into submission failures.
Metadata extraction is best-effort and must never gate native execution.

The CLI forwards output as it arrives and returns the native exit status. It
publishes a unique submission receipt without opening SQLite. The existing worker
and tracker ingest receipts idempotently and queue static analysis and AI work.
No new runtime traps are injected; completion comes from accounting. Existing
runtime records remain readable. Quiet/unrecognized output is not guessed into
a job association. Receipts are retained, including unresolved submissions.

## Implementation Plan

1. Add failing native-parity tests in `tests/test_sbatch_transparency.py`: untouched
   argv, stdin, byte output, native errors, help/test-only/quiet, wait exit status,
   descriptor inheritance, and broken bookkeeping. Run them before source edits.
2. Refactor `src/cslurm/cli/csbatch.py` and add a focused native runner under
   `src/cslurm/slurm/`. Keep metadata parsing separate from execution. Update old
   tests that intentionally required script rewriting.
3. Add durable receipt ingestion and connect it to `work.py` and `tracker.py`.
   Test replay, concurrent receipts, locked DB, and unchanged terminal records.
4. Update README and usage documentation with compatibility guarantees, receipt
   locations, limitations, and completion tracking. Run the full local suite and
   review the diff before committing.
5. Inspect mgmt status, preserve an additive code backup, fast-forward only the
   tracked source, compare hashes, and run fake tests plus isolated real Slurm
   parity tests. Use no cancellation commands, no production notification replay,
   no removal of existing files, and no production configuration edits.

## Verification Boundary

Passing the suite establishes the tested interface, not every plugin/version.
Python output observation can change stdout's terminal identity. A tracking-off
exec path must be available for descriptor/terminal-sensitive native plugins.
Accounting cannot uniquely recover every silent or interrupted submission, and
the current job-ID-keyed database is not a multi-cluster identity store.
