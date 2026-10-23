# SSH Workspaces

## Local behavior is unchanged

Start `cweb` on any machine as before. The default workspace is **Local (the cweb
host)**, not necessarily the computer running the browser. Its tasks, configuration,
logs and operations continue to use that process's `CSLURM_ROOT`. Starting cweb on
a management server and forwarding its port still works.

The server selector adds optional SSH workspaces; it does not replace local mode.
The SSH client and credentials belong to the account running cweb. To use your
laptop's SSH config, start cweb on your laptop and open its local access link.

## Add an existing server

1. First verify SSH from the **cweb host**: `ssh cluster`. Complete known-host
   verification and key/agent setup in your terminal, not the web page.
2. Open **Manage servers** beside the workspace selector. The SSH alias input
   offers searchable candidates from the cweb account's `~/.ssh/config`. Select
   an alias (such as `cluster`) and leave the field to start environment detection.
   The adjacent refresh icon also explicitly detects the entered alias. Only the
   selected server is contacted; opening the dialog does not probe all servers.
3. Detection prefers a Python 3.10+ environment with CleverSlurm and its web module.
   The environment selector lists the discovered interpreters; selecting one fills
   its Python path. Package version and location are shown below it. An empty
   display name is filled with the SSH alias. Expand **Advanced settings** to
   override the interpreter, data directory or new-install destination manually.
4. Check the remote data directory (`~/.cslurm` by default). Keep **Read-only
   connection** selected for browsing, or explicitly clear it to enable the
   existing non-cancellation operations.
5. Save, click **Connect**, then **View**. Use the selector to return to local mode.

### Discovery boundaries

Alias enumeration supports `Include`, multiple aliases per `Host`, and duplicate
removal. Wildcard and negated Host patterns are not selectable endpoints. Listing
does not evaluate `Match exec`, run `ProxyCommand`, resolve DNS, or read private
keys. Includes are enumerated conservatively even inside conditional blocks;
an offered alias may therefore be unusable in the current SSH context. The SSH
client, not this parser, applies effective connection settings. File/depth/size
limits and unreadable configuration are reported as partial results. Manual
aliases remain supported.

Detection searches PATH Python executables, the interpreter beside or in the
shebang of `cweb`/`cjobs`/`csbatch`, common home-directory Conda installations and
their environments, `~/.conda/environments.txt`, common virtualenv locations, and
the default CleverSlurm/uv tool environments. It checks at most 48 candidates,
with a 3-second per-interpreter and 25-second probe budget (50-second SSH request
budget including bootstrap). It does not recursively scan disks or source shell
activation scripts. Unusual installation locations still require manual paths.

The probe uses Python `-B`, module discovery and package metadata rather than
importing CleverSlurm startup code. It does not open or initialize task databases,
start trackers, synchronize Slurm, install packages or send notifications. Python
startup hooks and the account's ordinary SSH shell configuration still apply.

The detected data root comes from `CSLURM_ROOT` in the SSH environment or the
explicitly labeled `~/.cslurm` default. It is not inferred from the source checkout.
Existing or manually edited data roots are preserved. Selecting a different
interpreter is explicit; multiple Python versions/environments remain separate.
Detection failures, unsupported Python, and missing packages are distinct; a
failed or incomplete probe never proves that CleverSlurm is absent everywhere.
Connecting afterwards performs the actual web-API compatibility check. No
installation or upgrade is triggered by discovery.

The client uses `BatchMode=yes`, `StrictHostKeyChecking=yes`, no agent forwarding
and no inherited port forwards. Existing SSH configuration (including supported
jump-host setups) still applies. Passwords, private keys, host-key acceptance and
arbitrary SSH option strings are deliberately not web form fields. If SSH fails,
check your alias, agent, known_hosts and Python path in a terminal. A failure does
not mean the package is missing.

Connection starts a private loopback API helper in the chosen remote Python and
relays requests through SSH stdin/stdout. It does not require an already running
remote cweb, a remote access token, or a public listening port. The helper imports
the **existing remote installation**; no source files are uploaded or overwritten
when connecting. Connection does not initialize/migrate its database, start the
tracker, or dispatch notifications. Older packages without a compatible web API
are reported as incompatible, not automatically upgraded.

## Install on an uninstalled environment

The Install action appears only after a successful probe reports that CleverSlurm
is absent from the **selected Python environment**. This is not proof that no
other Python environment on the machine contains it. Check the interpreter first.

Choose a **new** directory beneath the remote home, defaulting to
`~/.local/share/cleverslurm`. Confirm the SSH alias to start installation. Progress
shows local packaging, then transfer and offline installation. The local cweb
Python needs `pip`, `setuptools` and `wheel`; the remote Python needs version 3.10+
and working `venv`/`ensurepip` support.
Packaging checks for at least 64 MiB free locally; installation checks for at
least 256 MiB at the destination and temporary-files location. These are minimum
checks, not reserved space. The retained install log includes venv/ensurepip
diagnostics as well as pip output.

The installer:

- Builds a wheel from the local installed CleverSlurm package and packaged web
  assets. It does not fetch GitHub HEAD or download remote Python dependencies.
- Transfers the wheel, verifies SHA-256, creates a private `venv` under the new
  directory, and installs with `--no-index --no-deps`.
- Refuses existing installation paths and symlink paths. It does not use sudo,
  alter system Python, uninstall another package or overwrite another checkout.
- Initializes the data directory only when the directory itself is new. An
  existing data directory, configuration and database remain untouched. If that
  existing directory has no database, the web page reports it unavailable until
  normal tracking initializes it; it is not silently repaired or migrated.
- Updates only the local server profile to use the new venv Python, then waits
  for an explicit connection. It does not enable cron, install Slurm shims, copy
  AI/Feishu secrets, send test messages or replay historical notifications.

Installation artifacts and failure logs are retained. The destination contains
the wheel, `venv/`, `install.log` and, on success, `installation.json`. Local
`install-artifacts/` contains the source snapshot, wheel and build log. After a
timeout or lost connection, inspect the destination before trying again: the
outcome may be uncertain. Do not blindly retry into or remove the old directory.
You can point the profile at a successfully created `venv/bin/python` manually.

For later CLI use, explicitly choose that venv's commands or add its `bin` to
PATH. Automatic tracking and Slurm shims remain separate opt-in steps described
in the [installation guide](../README.md#install). Removing a server from the web
list **does not uninstall anything**. To uninstall, stop its web connections and
follow the package's normal shim/tracker cleanup using that exact environment.
Data and configuration are not deleted by package uninstall.

## Workspace isolation and operations

Every request names its workspace. The server never changes a process-wide data
root to serve another request. Browser tabs can select different servers without
silently retargeting each other. Switching clears task selections, comparisons,
filters, dialogs and AI conversation display; old in-flight responses are rejected.
Switching is blocked during foreground mutations/background operation polling.

Tasks, logs, summaries, health and model/Feishu settings use the selected host.
Remote secrets use the existing masked settings API. Writes retain the existing
single-job confirmation and operation allowlist. Global `cweb --read-only` cannot
be bypassed by a writable remote profile. Cancellation additionally requires
`cweb --allow-cancel`; it is disabled by default. Demo mode cannot connect to real
servers or install packages.

Browsing and ordinary page refresh do not run Slurm synchronization. **Sync
Slurm** remains an explicit operation that can dispatch notifications according
to the selected server's existing configuration; it is not part of connecting.
No mutation is automatically retried after transport failure. Reconnect and
inspect the remote facts before repeating an uncertain operation.

Disconnect/removal is refused while the connection reports an active operation.
On a transport failure an already accepted remote operation may continue; losing
its local progress view does not roll it back. Reconnecting creates a new helper
and cannot restore that helper's in-memory operation IDs. Server profiles persist,
but connections and installation progress do not survive a local cweb restart.

Profiles are stored privately in `CSLURM_ROOT/servers.json` on the cweb host, not
inside the AI configuration. Edits require a matching revision and retain old
snapshots in `server-backups/`. Removing a profile only changes this registry.
The local access token now authorizes SSH access through these profiles; keep it
private and keep cweb bound to loopback.

## Tests

```bash
python3 -m pytest tests/test_web_remotes.py -q
# Optional: install Playwright and its browser in your development environment.
CSLURM_BROWSER_TEST=1 python3 -m pytest tests/test_web_remote_browser.py -q -s
```

The optional browser test requires Node to resolve `playwright/test`. Set
`CSLURM_BROWSER_CHANNEL=chrome` to use an existing Chrome installation, or configure
`PLAYWRIGHT_BROWSERS_PATH` for downloaded browsers when pytest isolates HOME.
All install, mutation and browser tests use fake SSH, isolated virtual environments
and temporary databases. Real-host verification should use read-only connections;
never test cancellation or replay notifications against existing production data.
