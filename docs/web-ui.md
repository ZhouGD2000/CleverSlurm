# Web Console

`cweb` adds a local web interface to the same database used by `cjobs`. It is a
single-user tool running as your Unix account, not a public or multi-user web
service. No Node.js, frontend build, web framework, or CDN is needed at runtime.
The packaged HTML, JavaScript, CSS, and Lucide icons are served locally.

The default workspace remains the host running `cweb`. The top-bar selector also
supports SSH servers using that host's existing SSH configuration. Add/connect
servers through the adjacent management button, then switch between local and
remote tasks, logs and settings. The alias input offers existing SSH-config hosts;
selecting one detects remote Python environments and existing CleverSlurm installs.
Manual paths remain available under Advanced settings. Uninstalled environments
support an explicitly confirmed, non-overwriting installation. See [SSH workspaces](multi-server.md)
for interpreter selection, installation prerequisites and safety boundaries.

The partition filter is a multi-select checkbox list of individual partitions.
For example, a recorded `CPU,CPU2` job matches either `CPU` or `CPU2`.
Multiple selections use OR matching with exact partition names (`CPU` does not
match `CPU2`). Selecting "All partitions" or clearing all checks removes the
partition restriction, including jobs without a recorded partition. Filtering
does not change the job's original partition value.

The state filter also supports multiple checkboxes, including individual states
and the Active / Abnormal exit groups. Selections within each filter use OR;
state and partition filters combine with AND. For example, selecting `RUNNING`
and `PENDING`, plus `CPU` and `CPU2`, lists running or pending jobs in either
partition. Overlapping state groups do not duplicate jobs. "All states" (or no
checked states) removes only the state restriction, leaving partition selections
in place. Sidebar shortcuts replace the state selection but keep the partition
filter. Refreshing records preserves both selections.

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

### Model and notification settings

The settings button in the top bar opens AI models, Feishu bots, and notification
policy. The current configuration comes directly from `config.toml`; no credentials
need to be re-entered. Named profiles can be added and edited without changing the
active setup. "Set as current" copies that profile into `[ai]` or
`[notification.feishu]`, including removing optional settings absent from the
chosen profile. CLI commands and subsequent tracker/summary processes use these
same sections. Already-running requests are not restarted.
Use the copy button to keep the current configuration as a named profile before
switching. Credentials are copied server-side without being returned to the browser.

Named profiles are stored as `[web.ai.<id>]` and `[web.feishu.<id>]`. Notification
policy remains global in `[notification]`. Feishu profiles are custom-bot webhook
destinations, not personal-account logins or an App ID/Secret messaging backend.
See [Feishu setup](feishu_setup.md) for delivery limitations.

API keys, webhook URLs, signing secrets, and additional request JSON are never
returned by the settings API. A blank secret field preserves the saved value;
"Clear saved value" explicitly removes it. Environment variables still take
precedence for runtime commands; the page lists server-side override variable
names, not their contents. Named `*_env` references must be available in the
environment of the process using them (web server and cron may differ).

Saving creates a private backup in `config-backups/`, then atomically replaces
`config.toml` with mode 0600. Unrelated configuration sections are preserved.
Concurrent web saves are serialized and stale revisions are rejected. External
editors should not write the file during a save; reload the page after manual
edits. Backups contain credentials and must remain private.

Saving or switching does not invoke AI, submit/cancel jobs, dispatch notifications,
or change historical notification records. Changing notification enablement or
destination can affect the next normal automatic dispatch, including any pending
messages; it does not suppress or purge that queue.

Connection tests are separate explicit actions against the selected **saved**
configuration. AI tests send only a fixed short prompt with the configured timeout
(30 seconds by default, capped at 45 seconds),
64-token output budget, no fallback models or retries, and no forced JSON format.
They verify basic connectivity, not every AI workflow. Feishu tests require a
confirmation and send one labeled test message, without reading or dispatching
the notification queue. Errors never return provider response bodies or secrets.
Read-only and demo modes disable saving, activation, and external tests.

Model tests and catalog discovery run in background subprocesses with a
60-second overall deadline. Closing the settings dialog does not block browsing.
Errors distinguish authentication, permissions, rate limits, DNS, TLS and timeout
failures, without returning provider response bodies. The health view records
requested/returned model IDs, elapsed time, fallback use and safe error codes,
never prompts, responses or credentials. Additional request JSON cannot override
`model`, `messages`, `system`, `stream`, `max_tokens` or `max_completion_tokens`.

#### Discover available models

In **AI models**, choose `openai` or `anthropic`, enter the Base URL and API key
(or a server-side key environment variable), then click the download icon next
to the model name, **获取可选模型**. A search field and model selector appear.
You can query a new, unsaved profile without entering a model name first. For an
existing profile, a blank key field uses the saved key. Search matches model IDs
and display names; selecting a result fills the model name without saving or
activating the profile. Save explicitly when ready.

Discovery runs on the web server (on the management host when using an SSH
tunnel), not in the browser. It sends authenticated GET requests to model-list
endpoints, not chat prompts, and does not write configuration, touch jobs, or
send notifications. OpenAI-format catalogs use Bearer authentication; Anthropic
catalogs use `x-api-key` and `anthropic-version`. Only HTTP 404/405 triggers a
fallback endpoint. Common Anthropic-compatible `/anthropic`, `/api/anthropic`,
and `/coding` prefixes can fall back to the same host's parent OpenAI catalog.

For nonstandard routing, set **模型列表地址（可选）** (`models_url`) to a full
model-list URL on the same origin as the Base URL. This overrides discovery
only, not chat requests. Remote URLs require HTTPS. Redirects are not followed
to avoid forwarding credentials. Requests have bounded timeouts and response
sizes; paginated catalogs stop at four pages or 2,000 models and show when the
list is incomplete. Changing the connection fields clears the previous list.

Some services do not expose catalogs, require additional permissions, or return
an empty list. Authentication, permission, rate-limit, and connection errors are
shown explicitly; manual model entry always remains available. A catalog entry
is not a guarantee that a model is free, enabled for the account, or suitable
for chat. Save the choice and use the separate connection test to check basic
chat compatibility. Discovery is disabled in read-only and demo modes.

### Tasks

- Task history: search Job ID, name, or submission command; filter by state and
  partition, UTC submission date range, working-directory substring and tag
  substring; paginate in groups of 25, 50, or 100. These filters combine with AND.
- Comparison: check 2 to 6 tasks and use the comparison button to inspect states,
  resource records, commands, directories, Git revisions and summaries side by side.
- Health: database integrity, cron enablement, last tracker success, free disk,
  notification counts, latest 100 work items and model calls. Individual failed
  work items can be requeued. The backup button creates and verifies a new private
  database backup on the server; it never overwrites an existing file. See
  [operations and recovery](operations.md).
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

Server management additionally supports saved SSH connections and a fixed,
confirmed installer; these are not arbitrary shell-command endpoints.
There is no bulk cancellation, submission, arbitrary command execution,
arbitrary configuration-file editor, or file deletion in the web interface. A
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
