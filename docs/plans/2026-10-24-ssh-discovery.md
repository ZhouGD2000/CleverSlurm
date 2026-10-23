# SSH Discovery Implementation Plan

**Goal:** Select configured SSH hosts and discover remote Python/CleverSlurm
environments without changing remote installations or the default local workspace.

**Architecture:** Read concrete aliases from the cweb account's SSH config (bounded
Include traversal, no Match execution). Run an explicit, bounded read-only SSH
probe for the selected alias. Prefer installed environments, expose multiple
choices and retain advanced manual fields. Discovery does not connect the bridge,
initialize databases, install packages, start trackers or dispatch notifications.

**Tech Stack:** Python standard library, existing authenticated HTTP API, plain
JavaScript UI, pytest and Playwright.

## Tasks

1. Add failing tests in `tests/test_web_discovery.py` for config parsing, bounded
   traversal, environment probes and failures. Implement `web/discovery.py` and
   self-contained `web/remote_discovery.py`; reuse strict SSH transport options.
2. Add authenticated discovery routes in `web/server.py`. Keep discovery local
   to the cweb host, with no workspace-header forwarding and no demo execution.
   Test authentication, permissions and unchanged local routing.
3. Update `static/index.html` and `static/servers.js`: searchable SSH aliases,
   selected-host detection, interpreter choices, stale response protection and
   advanced manual fields. Test selection, filling, saving and local workspace.
4. Run focused and full pytest, browser checks at desktop/mobile sizes and JS
   syntax checks. Update `docs/multi-server.md`.
5. Validate discovery and existing-job browsing against mgmt read-only. Compare
   installation/configuration evidence before and after; do not install, delete,
   cancel, synchronize or replay notifications. Commit only task-owned files.

## Discovery Boundaries

- Config listing never executes Match exec, ProxyCommand or shell snippets.
- Ignore wildcard/negated Host patterns; support explicit aliases and Include.
- Probe PATH, entry-point shebangs and bounded common Conda/venv/uv locations.
  Do not recursively search disks or source environment activation scripts.
- Use Python `-B`, avoid importing application startup code, and report Python
  version, package location/version and the environment/default data root.
- SSH failures and probe timeouts are not evidence of an uninstalled package.
- Never overwrite edited/saved settings silently; manual root overrides remain.
- Existing local mode, SSH host-key verification and explicit install confirmation
  remain unchanged. Credentials and private-key contents are never returned.

## Verification

- Added config/Include, discovery, failed candidates, distinct Python versions,
  non-importing probes, response limits, authentication, demo and concurrency tests.
- Full suite: 375 passed, 20 environment/opt-in skips.
- Playwright: alias selection, auto-fill, manual-root preservation, stale-response
  rejection, saving, remote/local switching, read-only gates, isolated offline
  installation and desktop/mobile layouts passed.
- Real-host read-only check: detected an existing Python 3.12.7 / CleverSlurm
  0.1.0 environment and browsed 5,528 tasks; local mode remained available.
  No remote install, configuration save, task synchronization or notification
  dispatch was performed.
- Before/after tracked-source and configuration hashes matched; notification
  counts were unchanged and the read-only SQLite quick check returned `ok`.
