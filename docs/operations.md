# Operations and Recovery

## Durable background work

`csbatch` publishes a submission receipt without accessing SQLite. Workers and
the tracker import it and atomically enqueue static analysis and (when enabled)
submission summary work. See [receipt recovery and limits](sbatch-compatibility.md).
The CLI returns without waiting for this work. There is one
worker pool per `CSLURM_ROOT`. `[work].concurrency` defaults to 2 and is clamped
to 1 through 8. A worker handles at most 100 items per invocation; later tracker
runs resume the backlog. Summary work waits for pending/running static analysis
of the same job. No runtime wrapper is added by this queue.

`ctrack` commits each new terminal-state event and its completion work item in
the same transaction. It wakes workers without waiting for AI. It does not scan
old terminal jobs for missing summaries or create historical notification work.
Work is deduplicated by kind, job ID and (for completion) state-change event ID.
Failures retry with exponential backoff beginning at 30 seconds, up to 5
attempts. Interrupted workers are recovered on the next worker launch.
Keep `ctrack auto on` enabled: a one-shot worker does not remain asleep waiting
for future retries, and a large backlog may need several tracking intervals.

```bash
cwork list           # latest 200 persisted work records
cwork run            # drain up to 100 eligible items now
cwork retry 123      # explicitly requeue one failed work-item ID
```

Retrying work may call the configured AI provider and create a notification for
a non-excluded job. It does not resubmit or cancel the Slurm job. Existing queued
work reads current configuration when executed; in-flight requests keep their
existing settings. Workers write diagnostic process output to `workers.log`.

## Notification safety

Notification claims and final status writes use short transactions. HTTP sends
occur after committing the claim, so a slow webhook does not hold a SQLite write
lock. A per-root dispatch lock and atomic claims prevent concurrent dispatchers
from sending the same pending notification.

The dispatcher makes one delivery attempt. A timeout, HTTP failure or interrupted
sender becomes `uncertain`, because the receiver might already have accepted the
message. It is not automatically retried. `cnotify dispatch --force` bypasses a
grouping time window, not this safety rule. Check the receiver before deciding
how to handle uncertain messages; exactly-once delivery cannot be guaranteed by
a webhook without receiver-supported idempotency.

For an explicit migration with **no historical replay**:

```bash
cdoctor backup
cdoctor freeze-history
```

`freeze-history` requires idle tracker, worker and dispatcher locks; it fails
instead of stopping those processes. It marks existing unsent notifications
`suppressed` and excludes all recorded non-active jobs (including `UNKNOWN`)
from future notification creation. Records are retained. Recorded active jobs
can still notify on future completion. This is a deliberate policy change, not
a routine health check; review state freshness before using it. During a code
upgrade, keep tracking paused/locked until the migration boundary is committed.

## Health and model diagnostics

`cdoctor health` and the web health view report database integrity, cron status,
last tracker success, stale tracking (over 5 minutes or no success yet), available
disk, queue counts, and recent work/model calls. Model telemetry contains model
IDs, interface format, elapsed milliseconds, fallback use and safe error codes.
It stores no prompt, response text, API key, URL or provider error body.
Telemetry is best-effort with a short write timeout; missing telemetry must not
make a successful AI request fail. Existing history predating the upgrade is
not reconstructed.

## Backup, verify and restore

```bash
cdoctor backup
cdoctor backup /path/on/local-disk/new-backup.sqlite
cdoctor verify /path/on/local-disk/new-backup.sqlite
cdoctor restore /path/on/local-disk/new-backup.sqlite /path/on/local-disk/new-root
```

Backup uses SQLite's online backup API, then full integrity verification. The
default destination is `backups/` under the configured root, with a unique name
and mode 0600. Existing files and symlinks are refused. Failed/partial backups
are retained for inspection, not automatically removed. Backups contain private
job data and may contain private log-derived summaries.

Restore verifies the source and creates a **new** directory containing
`db.sqlite`; it never overwrites the live database. It does not copy credentials,
configuration or cron entries. Inspect it first with tracking disabled:

```bash
CSLURM_ROOT=/path/on/local-disk/new-root cweb --read-only --port 8767
```

Switching production to a restored database is a separate maintenance operation:
stop/lock all writers, preserve the original database and configuration, and
establish the notification boundary before re-enabling tracking. Do not open
SQLite on NFS. See [database safety](database-safety.md).
