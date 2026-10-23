# Multi-server Console Implementation Plan

**Goal:** Preserve the existing cweb-host workspace and add SSH-backed remote
workspaces with explicit, non-overwriting installation from the local package.

**Architecture:** The browser selects a workspace per request; it never changes
process-wide CSLURM_ROOT. A local registry stores SSH aliases and explicit Python,
root and installation paths, without credentials. A persistent SSH stdio bridge
starts a private loopback ConsoleServer using the installed remote package and
forwards only allowlisted API requests. It needs no public port, database copy,
remote source update or separately configured remote token.

**Tech Stack:** Python standard library, OpenSSH, existing HTML/JS/CSS and Lucide
assets, pytest, Playwright for browser verification.

## Approved safety and lifecycle

- Default workspace remains the host running cweb, including remote cweb usage.
- SSH uses existing config/agent, BatchMode and strict known-host checking.
- Each request carries its workspace; switching invalidates old UI responses.
- Connection reads existing data without schema initialization or cron changes.
- Installation requires a fresh destination and explicit confirmation, creates a
  private virtual environment, and installs a wheel built from this local package.
  It never overwrites an existing installation/configuration or enables cron/shims.
- Failures retain installation artifacts; removal removes only a local profile.
- Remote operations retain read-only, demo and cancellation gates; no automatic
  retries of mutations. Disconnect is refused during active operations.
- Validation on mgmt only connects to its existing installation and reads data.
  Production code/configuration, jobs and notification queues remain unchanged.

## Task 1: Registry and SSH boundary

Files: `src/cslurm/web/remotes.py`, `tests/test_web_remotes.py`.
Write failing tests for validation, private atomic storage, duplicate aliases,
profile revisions, command quoting, strict SSH flags and no-credential storage.
Run `python3 -m pytest tests/test_web_remotes.py -q`, implement and rerun.

## Task 2: Bridge and installation

Files: `src/cslurm/web/remote_bridge.py`, `src/cslurm/web/remote_install.py`,
`src/cslurm/web/remotes.py`, `tests/test_web_remotes.py`.
Test actual bridge subprocesses through fake SSH: handshake, missing package,
timeouts/disconnects, bounded messages, read-only calls, operation authorization,
no retry and unchanged remote files. Test fresh venv installation and refusal
of existing/symlink destinations. Install artifacts use an explicit hash and no
remote dependency downloads. Install progress is polled as a background action.

## Task 3: HTTP routing

Files: `src/cslurm/web/server.py`, `tests/test_web_remotes.py`.
Add authenticated management routes and per-request workspace routing before
local database checks. Test default-local behavior, host/origin/token checks,
disabled mutation in demo/read-only mode and target isolation across clients.

## Task 4: User interface

Files: `src/cslurm/web/static/{index.html,app.js,servers.js,style.css}`.
Add compact workspace selection and a server management dialog with explicit
connect/disconnect/install actions and status/progress. Preserve existing job,
health and settings views. On switching, clear job/detail/compare/chat state and
invalidate stale fetches and operation polling. Prevent switching during active
mutations. Verify desktop/mobile layouts and workflows in browser.

## Task 5: Documentation and verification

Files: `README.md`, `docs/web-ui.md`, `docs/multi-server.md`.
Document SSH prerequisites, interpreter selection, install paths, data isolation,
failure recovery, lifecycle and uninstall ownership. Run all local tests, JS
syntax checks and browser tests. Commit coherent changes. Verify against mgmt
read-only, with before/after Git/config hashes and database/notification counts.
Do not deploy over its existing installation. Leave a local cweb running for use.
