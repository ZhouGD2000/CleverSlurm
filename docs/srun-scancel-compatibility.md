# Native srun and scancel compatibility

`csrun` and `cscancel` invoke native Slurm before performing bookkeeping. All
arguments are passed unchanged, including empty, invalid, unknown/plugin,
heterogeneous, array, step, filter and interactive arguments. Native Slurm alone
decides whether they are supported. The child environment, PATH, working
directory, stdin/stdout/stderr and inheritable file descriptors are preserved.
The CLI does not capture, decode or delay native output. Native return codes and
signal termination are preserved instead of converted into Python tracebacks.

Native command lookup skips CleverSlurm-managed shims and renamed console scripts.
`CSLURM_SRUN` and `CSLURM_SCANCEL` may explicitly select original executable paths;
never point them back at wrappers. Use `cshim install` instead of overwriting a
system binary. Keep that shim directory ahead of Slurm in PATH, including in SSH
and subprocess environments. `cshim remove` should precede package uninstall.

## Tracking without blocking on SQLite

After native execution, the CLI publishes a private, uniquely named JSON receipt.
No SQLite connection, schema migration, AI call or notification send happens in
the CLI. Workers or `ctrack` ingest it later. Unavailable storage, malformed
configuration and database locks cannot replace the native command's result.
Bookkeeping remains best-effort: an unwritable spool or a wrapper killed before
publication can lose that invocation's record. Filesystem writes still have
filesystem latency; this does not promise bounded latency for a hung mount.

`csrun` records `SRUN_FINISHED` after native srun exits. Outside an allocation,
the tracking ID is `local-...`, not a guessed real Slurm ID. Within an allocation,
the event belongs to the parent job (or array task) without replacing its metadata
or declaring the entire job finished. Slurm accounting remains authoritative.
Streaming output is not duplicated into receipts in CLI mode; use normal Slurm
output files. Python helpers retain optional captured text and immediate
best-effort ingestion; pass `defer=True` for asynchronous behavior.

## Cancellation notes and audit semantics

The former CleverSlurm-only `--note` is removed. Every CLI argument belongs to
native scancel. To annotate an **intentional** cancellation, use an environment
variable rather than an extra option:

```bash
CSLURM_CANCEL_NOTE="reason for cancelling" cscancel JOB_ID
```

The Python API accepts `cancel_job(argv, note="reason")`. The web console uses the
environment variable and retains its single-job confirmation and permission gate.

- Successful explicit whole-job requests: `CANCEL_REQUESTED` for each clear ID.
- Explicit signals or step targets: `SIGNAL_REQUESTED`, not assumed cancellation.
- Filters, interactive confirmation, ranges, cluster selectors, unknown options
  or `SCANCEL_*` selector environments: global `SCANCEL_REQUESTED`, without guessing
  which jobs were affected or whether an interactive confirmation was accepted.
- Native failure: global `SCANCEL_FAILED`. This may include partial success.
- Help/version/usage queries: no request event.

Request events never mark jobs `CANCELLED`. Receipt ingestion never executes or
retries scancel, and does not itself create notifications. Final state is obtained
by the tracker. Broad native scancel options are deliberately not blocked: they
retain exactly the same operational risk as using Slurm directly.

## Signals and limits

Foreground terminal Ctrl+C/Ctrl+backslash already reach the native child through
its process group; the parent does not send a second copy. This matters because
[srun treats repeated interrupts specially](https://slurm.schedmd.com/srun.html).
Without a foreground terminal, the parent forwards INT/QUIT; TERM/HUP/USR1/USR2
are also forwarded. Inherited ignored signals remain ignored.

Tracking mode adds a parent process, so process trees, PID-directed signals,
timing and every site's plugin/terminal integration cannot be perfectly identical.
In particular, directly sending INT/QUIT to only the wrapper PID while it is in
the foreground terminal cannot be distinguished from a terminal-generated signal
by Python's signal handler. Use native exec for exact PID-sensitive behavior:

```bash
CSLURM_TRACK_COMMANDS=false csrun --pty bash
```

The same switch works for `cscancel`. It replaces the wrapper process with native
Slurm and disables receipt tracking for that invocation. It does not modify the
stored configuration. For `csbatch`, use `CSLURM_TRACK_SUBMISSIONS=false` instead.

## Verification

Fake tests cover unchanged arguments, binary output, inherited descriptors,
interactive prompts, PTYs and Ctrl+C, native failures/signals, locked/unwritable
storage, conservative audits, replay deduplication and invalid receipt isolation.
`tests/test_real_command_parity.py` compares native and wrapped information output
and runs two short srun jobs with stdin/stdout/stderr and exit codes 0/7.
**Real scancel tests only use `--help`, `--usage`, and `--version`.** No real
cancellation or signal request is made. All mutation cases use fake executables.
See the native [scancel manual](https://slurm.schedmd.com/scancel.html) for selector
and signal semantics; the installed cluster version remains authoritative.
