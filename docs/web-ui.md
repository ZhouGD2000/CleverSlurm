# Web Console

`cweb` adds a local web interface to the same database used by `cjobs`. It is a
single-user tool running as your Unix account, not a public or multi-user web
service. No Node.js, frontend build, web framework, or CDN is needed at runtime.
The packaged HTML, JavaScript, CSS, and Lucide icons are served locally.

## Install and start

Use the same environment as your other CleverSlurm commands:

```bash
python3 -m pip install -e .
cweb
```

With uv:

```bash
uv venv .venv
uv pip install --python .venv -e .
.venv/bin/cweb
```

From a source checkout without installing:

```bash
PYTHONPATH=src python3 -m cslurm.cli.cweb
```

Open the full access link printed by the command. Its random token is after
`#token=`. The browser removes the fragment after reading it and keeps the token
in that tab's session storage. The server never prints HTTP access logs. Keep
the link private; anyone with that token and access to the port has the same
web-console permissions. Restarting the server invalidates previous tokens.

Options:

```bash
cweb --port 8766       # choose another port; --port 0 selects an available port
cweb --read-only       # disable all web-initiated operations, including AI
cweb --allow-cancel    # explicitly enable confirmed single-job cancellation
cweb --demo            # isolated synthetic data; Slurm/AI/notifications disabled
```

`CSLURM_ROOT` selects the database and configuration just as it does for the CLI:

```bash
CSLURM_ROOT=/path/to/cslurm-data cweb
```

Startup checks database integrity before initializing/migrating the schema in
normal mode. Browsing always uses read-only SQLite connections. `--read-only`
also skips schema initialization/migration and requires an existing compatible
database. A failed integrity check disables database browsing and operations,
and displays an error instead of misleading task records. No automatic recovery,
replacement, or deletion occurs. Back up and repair the database separately,
then restart `cweb`. `--demo` creates its own temporary example database (even
with `--read-only`), never loads your usual configuration, and removes only its
own temporary data when stopped.

## Use on a Slurm management host

Run the service on the host that can read the database/log files and execute
Slurm commands. In a terminal connected to that host:

```bash
ssh mgmt
cd /path/to/cleverslurm
PYTHONPATH=src python3 -m cslurm.cli.cweb --port 8765
```

Leave that command running. In a separate terminal on your computer:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 8765:127.0.0.1:8765 mgmt
```

Open the access link printed on the server in your local browser. If your local
port 8765 is occupied, forward a different local port:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 8766:127.0.0.1:8765 mgmt
```

Change only the port in the browser URL to 8766; keep the server's token.
Do not bind the service to `0.0.0.0`, publish it through a public reverse proxy,
or copy private job databases/configuration to a web hosting platform. `cweb`
deliberately exposes no public-bind option. SSH handles authentication and
encryption between your computer and the server.

## Views

- Task history: search Job ID, name, or submission command; filter by state and
  partition; paginate in groups of 25, 50, or 100.
- Active tasks and failures: separate filters over the recorded scheduler state.
- Overview: partition, node, elapsed time, memory, CPUs, working directory,
  submission command, and stored submission/completion summaries.
- Logs: recorded stdout/stderr, or the default Slurm output path if neither is
  recorded. Read at most 128 KiB per file and show its last 200 lines. Missing
  logs and non-regular files are shown explicitly; there is no arbitrary file
  browser or file-editing endpoint.
- Files and commands: statically identified files/entry points and submission
  paths. These are records, not a complete runtime filesystem trace.
- Events and notifications: latest 200 rows per category, including notification
  delivery status and errors. Truncation is indicated in the view.
- JSON export: exports the selected task and the currently returned detail
  records. It is not a full database backup and does not include log contents.

`COMPLETED` is a Slurm state, not a guarantee of scientific convergence. Program
analysis, when present, is displayed separately. Failure counts refer to Slurm
failure states, not all semantic/log warnings.

## Operations

All operations require the session token, execute through an allowlisted CLI
entry point without a shell, and run in the background. Browsing remains
available. One operation is allowed at a time per server. An operation is not
automatically retried, is limited to 300 seconds, and returns success or failure
to the UI. Operation status is kept in memory for the latest 50 operations;
restart loses this progress history, not the underlying database records.

| Control | Effect |
| --- | --- |
| Refresh records / automatic refresh | Read SQLite only; automatic refresh is every 15 seconds while the tab is visible. |
| Sync Slurm | Run `ctrack` once, respecting its cron lock. This can also analyze completed jobs and dispatch configured Feishu notifications. |
| Update summary | Run `csummarize` for the selected submission/completion summary, using the existing model configuration. This sends facts to that provider and replaces that stored summary. |
| AI question | Run `cjobs ask` over the most recent 10 recorded jobs, with the existing deterministic fallback on model failure. It does not execute commands proposed by the model. |
| Cancel task | Disabled unless started with `--allow-cancel`. Requires typing the exact selected Job ID. Only a known active numeric job or individual array-task ID is accepted. Recorded array parents, lists, ranges, flags, and unknown/terminal jobs are rejected. |

There is no bulk cancellation, submission, arbitrary command execution,
configuration/credential editor, or file deletion in the web interface. A
successful cancellation means Slurm accepted the request; the UI does not mark
the task `CANCELLED` until a subsequent tracking update confirms it.

Automatic page refresh does not replace `ctrack auto on`. Keep the existing
tracker enabled when you want job state and notifications to update without
clicking Sync Slurm. `cweb` does not install or modify cron entries.

## Security and lifecycle

The server listens only on IPv4 loopback, requires a random bearer token for
every API request, validates the Host and Origin, rejects cross-origin requests,
uses a restrictive content security policy, and renders stored job text as text,
not HTML. Secrets from the model/Feishu configuration are not served to the
browser. Job logs and exported task records may themselves contain private
information; treat them as private data.

Stop the console with Ctrl+C in its server terminal, and stop the SSH tunnel in
its own terminal. This does not disable `ctrack` cron or cancel Slurm tasks.
It terminates only unfinished helper subprocesses started by that web server.
Already committed database updates or accepted cancellation requests are not
rolled back; check task events before repeating an interrupted operation.
Before uninstalling, stop `cweb`; a running Python process does not disappear
when its package is uninstalled. Then follow the normal shim/tracker cleanup:

```bash
cshim remove
ctrack auto off
python3 -m pip uninstall CleverSlurm
# Or, for the chosen uv environment:
# uv pip uninstall --python .venv CleverSlurm
```

## Verification

```bash
python3 -m pytest tests/test_web.py -q
node --check src/cslurm/web/static/app.js  # optional development check
cweb --demo
```

Web tests use temporary databases and fake Slurm executables. They cover
authentication, origin/host validation, input validation, bounded log reads,
pagination, read-only mode, single-job cancellation and the tracker lock. No
existing real Slurm job is cancelled by these tests.
