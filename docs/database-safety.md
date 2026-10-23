# Database storage and recovery

## Where the database may run

Run database commands (`csbatch`, `cjobs`, `ctrack`, `csummarize`, `cnotify`,
and `cweb`) on the management host where `CSLURM_ROOT/db.sqlite` is stored
on a local filesystem. Use an SSH tunnel for the web console. The same home
directory can be local XFS on that host and NFS on compute nodes: checking
only the management host's mount is not sufficient.

On Linux, CleverSlurm checks `/proc/self/mountinfo`, resolves symlinks and
selects the longest matching mountpoint. Database access on NFS, SMB/CIFS,
Lustre, Ceph, GPFS, 9p and FUSE mounts is refused before opening SQLite.
This is a guard against known network filesystems, not a universal storage
certification; administrators must verify other platforms and filesystems.

New databases use SQLite's rollback journal (`DELETE`) with `synchronous=FULL`.
Connections are closed on leaving their context. Busy-timeout and short-write
transaction retries remain enabled. The program no longer forces WAL or
changes journal modes while other clients might be running.

Within an allocation, `csrun` writes a uniquely named JSON record under
`CSLURM_ROOT/spool/csrun/`, without opening SQLite. It does the same on a
network filesystem outside an allocation. Records are flushed before atomic
publication. `ctrack` imports them on the management host and moves them to
`spool/csrun/ingested/` after committing. Replaying the same record does not
duplicate its event. An individual step's exit status does not replace batch
metadata or mark the entire allocation as completed. Slurm accounting remains
the authority for the allocation's state.

## Existing WAL databases

WAL requires all database clients to run on the same host. SQLite versions
before 3.51.3 also have a documented WAL-reset race (patched backports include
3.44.6 and 3.50.7). Do not merely increase busy timeouts or repeatedly rebuild
a damaged WAL database without removing the unsafe access pattern.

References: [SQLite WAL restrictions and WAL-reset bug](https://sqlite.org/wal.html)
and [SQLite corruption causes and backup rules](https://sqlite.org/howtocorrupt.html).

For a **healthy** legacy WAL database:

1. Stop CleverSlurm database clients on all hosts and disable its tracking cron.
   Do not stop Slurm jobs. Upgrade compute-node clients before resuming tracking.
2. Preserve the database and any `-wal`, `-shm`, and `-journal` files together in
   a new private backup directory, with hashes. Never remove a hot journal.
   Raw copies require quiesced writers; for live healthy databases use SQLite's
   backup API instead of copying only the main file.
3. On the host with local storage, run `PRAGMA integrity_check` using SQLite.
   Proceed only when the complete result is `ok`.
4. Run `PRAGMA journal_mode=DELETE` while all other clients remain stopped,
   then check integrity again. The expected journal mode is `delete`.
5. Start updated clients and enable tracking if it was previously enabled.

CleverSlurm refuses to silently migrate an existing WAL database during normal
operation. This is intentional: a journal-mode change is not corruption repair.

## Damaged databases

Work only on copies and keep every original file. SQLite `.recover
--ignore-freelist` can recover physical pages into a new database, but an `ok`
integrity check on that output is not proof that its records are correct.
Validate identities, timestamps, column layouts, unique keys, JSON fields and
cross-table references. Retain unclassifiable records in a quarantine table and
preserve the complete recovery SQL. Avoid recovering freelist contents by
default, because they can resurrect intentionally deleted records.

Reconstruct missing submissions only from surviving submission events or
script snapshots. Leave unknown facts empty and record their provenance. Query
`sacct` read-only to refresh authoritative states, including array-parent
aggregation; do not execute stored commands, resubmit jobs, or dispatch all old
notifications during recovery. Keep notification IDs, statuses and dedupe keys
so previously sent messages are not sent again.

Before installing a validated replacement, recheck that no database clients
are open and that the original database and journal hashes have not changed.
Move the original files into the backup directory rather than deleting them.
Verify integrity and real job details through a read-only `cweb` instance
before re-enabling write operations. Keep a recovery report with copied,
reconstructed and quarantined counts; do not claim that all historical data is
recoverable just because SQLite reports `ok`.
