# CleverSlurm

CleverSlurm is a small, deterministic Slurm job tracking tool. It wraps common Slurm commands, records what was submitted and what happened, and stores job facts in SQLite so they can be queried later.

The design rule is simple: deterministic code records facts; AI only summarizes recorded facts.

## Current Features

- `csbatch`: invokes native `sbatch` with unchanged arguments and scripts, forwards native output and exit status, and publishes submission receipts for background tracking. No forced `--parsable`, script replacement, or runtime trap. See [sbatch compatibility](docs/sbatch-compatibility.md).
- `csrun`: delegates unchanged arguments and terminal streams to `srun`, then publishes a completion receipt without waiting for SQLite.
- `cscancel`: delegates unchanged arguments and terminal streams to `scancel`, then publishes a conservative request audit. Optional notes use `CSLURM_CANCEL_NOTE`.
- `ctrack`: polls `sacct` for known jobs and updates state, exit code, derived exit code, elapsed time, memory, and node list.
- `cjobs`: queries sacct-like recent history, squeue-like active queues, job details, events, files, recorded commands, log tails, and AI answers over recent job facts.
- `cweb`: web console for its host's tasks plus optional SSH server workspaces, logs, summaries, AI/Feishu settings and gated operations. Offers aliases from the host's SSH config and read-only Python/CleverSlurm environment discovery. Supports confirmed installation into a new remote virtual environment without overwriting existing installations. See [multi-server setup](docs/multi-server.md).
- `csummarize`: sends curated job facts to a configured OpenAI-compatible or Anthropic-compatible model API and stores structured AI summaries.
- Feishu notifications: when `ctrack` sees a terminal job state, it records deterministic/semantic analysis, queues a notification, and immediately sends hard failures through a Feishu/Lark custom bot when configured.
- Static script analysis: after `csbatch`, a detached worker identifies obvious MATLAB and Python entry commands and records their entry files without wrapping the runtime process.
- Runtime command ingestion: imports JSONL records from `commands.log` into `job_commands` when such logs are produced externally.
- Local fake-Slurm tests: the test suite does not require Slurm.

## Install

### With pip

From the repository root, install into the active Python environment:

```bash
python3 -m pip install -e .
command -v csbatch cjobs ctrack cshim
```

Use the same `python3` environment for install, runtime, and uninstall. `pip install -e .` installs console scripts into that environment's scripts directory, such as `csbatch`, `cjobs`, `ctrack`, and `cshim`.

### With uv

Use `uv` with a dedicated virtual environment when you want installation, command scripts, cron, and uninstall to be tied to one explicit environment:

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e .
command -v csbatch cjobs ctrack cshim
```

Without activating the environment, point `uv` at the environment explicitly:

```bash
uv pip install --python .venv -e .
.venv/bin/cjobs recent
```

You can also run commands through uv from the repository root:

```bash
uv run --project . cjobs recent
uv run --project . csbatch job.slurm
```

For long-running use on a cluster, prefer putting the uv environment's scripts directory on `PATH`:

```bash
export PATH=/path/to/cleverslurm/.venv/bin:$PATH
```

Installation does not start automatic tracking. Enable it explicitly after install when you want CleverSlurm to refresh job state and dispatch Feishu notifications from cron. For a normal installed environment:

```bash
ctrack auto on
```

For source checkouts, cron, or hosts with multiple Python environments, specify the repository and Python executable explicitly so cron does not depend on its working directory or a different default `python3`:

```bash
ctrack auto on --repo /path/to/cleverslurm --python /path/to/python3
ctrack auto status
```

For a uv environment, use the environment's Python:

```bash
ctrack auto on --repo /path/to/cleverslurm --python /path/to/cleverslurm/.venv/bin/python
ctrack auto status
```

If you want scripts that call `sbatch`, `srun`, or `scancel` by their Slurm names to be tracked, install command shims after the package install:

```bash
cshim install
cshim status
```

If the default scripts directory is not the one that appears first in `PATH`, specify it explicitly and use the same value for status and removal:

```bash
cshim install --bin-dir /path/to/env/bin
cshim status --bin-dir /path/to/env/bin
```

For a uv environment, that directory is usually:

```bash
cshim install --bin-dir /path/to/cleverslurm/.venv/bin
cshim status --bin-dir /path/to/cleverslurm/.venv/bin
```

Uninstall the editable package and console scripts:

```bash
cshim remove
ctrack auto off
python3 -m pip uninstall CleverSlurm
```

With uv and an activated environment:

```bash
cshim remove
ctrack auto off
uv pip uninstall CleverSlurm
```

Without activation:

```bash
/path/to/cleverslurm/.venv/bin/cshim remove --bin-dir /path/to/cleverslurm/.venv/bin
/path/to/cleverslurm/.venv/bin/ctrack auto off
uv pip uninstall --python /path/to/cleverslurm/.venv CleverSlurm
```

If shims were installed with an explicit directory, remove them with the same directory before uninstalling:

```bash
cshim remove --bin-dir /path/to/env/bin
ctrack auto off
python3 -m pip uninstall CleverSlurm
```

Run `cshim remove` and `ctrack auto off` before uninstalling because `pip uninstall` does not run CleverSlurm cleanup hooks. `cshim remove` only deletes wrappers that contain the CleverSlurm-managed marker. If the managed cron entry is left behind, it checks whether `cslurm` is still importable before every tracker run; if not, it removes only the marked CleverSlurm block from the user's crontab and exits. In source mode, the source tree passed with `--repo` still counts as importable code while it remains on disk. The uninstall command removes the installed Python package entry and command wrappers such as `csbatch`, `csrun`, `cjobs`, and `cnotify` from that Python environment. It does not remove runtime data or config under `~/.cslurm/`. Remove that directory separately only when you intentionally want to delete the local job database, copied scripts, logs, and secrets/config.

For development without installing console scripts, run with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 -m cslurm.cli.csbatch job.slurm
```

## Use As A Codex Skill

This repository also includes a Codex skill wrapper at `skills/cleverslurm/`. The skill teaches an agent how to use and maintain CleverSlurm safely, but it does not replace the Python package. Install the package normally when you need the actual CLI commands.

Install or copy the skill into your Codex skills directory:

```bash
mkdir -p ~/.codex/skills
cp -R skills/cleverslurm ~/.codex/skills/
```

Then invoke it explicitly as `$cleverslurm`, or let Codex use it automatically for CleverSlurm, Slurm tracking, `csbatch`, `cjobs`, `ctrack`, `cshim`, AI summaries, and Feishu notification tasks.

The public repository is:

```text
git@github.com:ZhouGD2000/CleverSlurm.git
```

## Configuration

By default CleverSlurm writes to:

```text
~/.cslurm/
  db.sqlite
  jobs/
  store/
  config.toml
```

Use `CSLURM_ROOT` to isolate a test database:

```bash
CSLURM_ROOT=/tmp/cleverslurm-test csbatch job.slurm
```

Example `~/.cslurm/config.toml`:

```toml
[ai]
format = "openai"
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
model = "deepseek-v4-pro"
max_tokens = "512"
auto_summary = "true"
request_retries = "1"

[notification]
enabled = "true"
auto_dispatch = "true"
ai_analysis = "false"

[notification.feishu]
webhook_url_env = "CSLURM_FEISHU_WEBHOOK"
secret_env = "CSLURM_FEISHU_SECRET"
message_format = "card"
batch_window_minutes = "30"
immediate_group_threshold = "10"

[tracking]
static_analysis = "true"
```

Do not commit API keys. Put them in environment variables such as `DEEPSEEK_API_KEY` or `KIMI_API_KEY`.

Enable Feishu without writing secrets to disk:

```bash
export CSLURM_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export CSLURM_FEISHU_SECRET="..."
```

## Basic Usage

### Web console

```bash
cweb                 # open the private access link printed in the terminal
cweb --read-only     # browsing only
cweb --demo          # isolated example data, without Slurm or model requests
```

For server data, run `cweb` on the Slurm management host and forward its loopback
port with `ssh -N -L 8765:127.0.0.1:8765 mgmt`. Open the server's printed access
link locally. Cancellation is disabled by default; `--allow-cancel` enables only
explicitly confirmed single-job requests, never bulk cancellation.

See [Web console setup, security, and operations](docs/web-ui.md) for pip/uv
installation, SSH forwarding, background operations, and shutdown/uninstall.

### Command line

Submit a batch script:

```bash
csbatch job.slurm
csbatch -p CPU2 --time=00:01:00 job.slurm arg1 arg2
csbatch -p CPU2 --wrap "hostname && python3 script.py"
```

Intercept scripts that call Slurm command names directly:

```bash
cshim install
cshim status
```

`alias sbatch=csbatch` only affects commands typed through an interactive shell. It does not affect Python code such as `subprocess.run(["sbatch", "job.slurm"])`, and it is usually not loaded for commands sent as `ssh host '...'`. `cshim install` creates user-level `sbatch`, `srun`, and `scancel` wrapper executables in the same directory as `csbatch`, so PATH lookup works for subprocesses too. The wrappers export `CSLURM_SBATCH`, `CSLURM_SRUN`, and `CSLURM_SCANCEL` to the original Slurm commands, then exec `csbatch`, `csrun`, or `cscancel`.

When CleverSlurm itself calls Slurm, it skips its own managed shims and calls the real Slurm command found later in `PATH`. This prevents nested `csbatch -> sbatch shim -> csbatch` submissions.

Remove only CleverSlurm-managed shims:

```bash
cshim remove
```

After native `sbatch` returns, the CLI publishes a receipt under `~/.cslurm/spool/sbatch/` without opening SQLite. A background worker imports the job and atomically queues static analysis and the enabled AI summary. `ctrack` also imports pending receipts, so keep `ctrack auto on` enabled. A newly submitted job may briefly be absent from `cjobs` until ingestion completes. `cjobs events <job_id>` shows `AI_SUMMARY_QUEUED`; `cjobs summary <job_id>` displays the completed summary. Work status and retries are visible with `cwork list`; process diagnostics go to `~/.cslurm/workers.log`.

The static worker reads the available script snapshot from `~/.cslurm/jobs/<job_id>/original.slurm`, recognizes common MATLAB and Python entry forms such as `$EXE -r Dinf`, `matlab -batch Dinf`, `$PY run.py`, and `srun python3 train.py`, then records rows visible through `cjobs commands <job_id>` and `cjobs files <job_id>`. Analysis uses the effective job working directory. It does not inject runtime wrappers, traps, or change the batch job's `PATH`. Stdin and unrecognized plugin syntax may not have an archived script; see the compatibility document for metadata limits.

Track known jobs:

```bash
ctrack
```

For automatic state refresh and Feishu dispatch, let `ctrack` manage a cron entry:

```bash
ctrack auto on
ctrack auto status
ctrack auto restart
ctrack auto off
```

The generated cron entry uses `flock`, runs once per minute, writes logs to `~/.cslurm/ctrack.log`, and only edits the marked CleverSlurm block in the user's crontab. Each run first checks whether `cslurm` can still be imported with the generated environment; if that check fails, the cron command removes its own marked block and exits.

Inspect jobs:

```bash
cjobs recent
cjobs queue
cjobs show 46644
cjobs events 46644
cjobs files 46644
cjobs commands 46644
cjobs logs 46644 --tail 100
cjobs notifications 46644
cjobs summary 46644
cjobs summary 46644 --completion
cjobs ask "最近完成了什么任务？都是些什么工作？"
```

`cjobs recent` uses a `sacct`-like history table over recorded CleverSlurm facts, so completed jobs are included:

```text
JobID JobName Partition Account State ExitCode Elapsed MaxRSS NodeList Submit
```

Use `cjobs recent --no-header` for script-friendly output.

`cjobs queue` uses an `squeue`-like table for active or not-yet-terminal tracked jobs:

```text
JOBID PARTITION NAME USER ST TIME NODES NODELIST(REASON)
```

Use `cjobs queue --no-header` for script-friendly output. Both commands read the CleverSlurm database rather than live Slurm directly, so fields that were not recorded for older jobs may be blank.

Run a direct Slurm command:

```bash
csrun python script.py
```

Summarize a job with AI:

```bash
csummarize 46644
csummarize 46644 --completion
```

`cjobs summary` only reads existing stored summaries. `csummarize` sends a new AI request and updates the stored summary. Without `--completion`, both commands use the submission summary (`summary_json`); with `--completion`, they use the completion summary (`completion_summary_json`).

Temporarily override the AI model:

```bash
csummarize 46644 --model deepseek-v4-pro --max-tokens 512
```

Ask AI about recent jobs:

```bash
cjobs ask "最近完成了什么任务？都是些什么工作？"
cjobs ask "最近失败的任务有哪些，原因是什么？" -n 20
```

`cjobs ask` sends only recorded database facts to the model. It uses a natural-language request with a capped answer length, does not force provider JSON mode, and does not ask AI to infer Slurm job ids, states, exit codes, paths, or commands from memory. If the AI request fails or returns an empty answer, `cjobs ask` prints a deterministic summary from the local job database instead of a traceback.

Dispatch pending Feishu notifications manually:

```bash
cnotify pending
cnotify dispatch
cnotify dispatch --mode batch --force
cnotify dispatch --mode digest --force
cnotify dispatch --mode all
```

`ctrack` records terminal-state changes and queues completion analysis without waiting for AI. A bounded background worker analyzes them; a following tracker run dispatches ready notifications and due batch/digest summaries. With minute-based cron, delivery normally follows analysis by up to another tracking interval. `--force` only bypasses the batch/digest time window.

## Health and Background Work

```bash
cdoctor health
cwork list
cwork retry 123                 # one failed work item, not a Slurm job ID
cdoctor backup                 # new private, verified database backup
cdoctor verify /path/to/backup.sqlite
cdoctor restore /path/to/backup.sqlite /path/to/new-recovery-directory
```

The web console includes health, failed-work retry, database backup, combined
date/directory/tag filters and comparison of 2 to 6 jobs. Submission analysis and
summaries use a durable queue with bounded concurrency (default 2). Enable
`ctrack auto on` so retries and queued work are revisited automatically. Restore
always uses a new directory and never replaces the live database. See
[operations and recovery](docs/operations.md) for notification safety and recovery.

## Support Boundaries

`csbatch` delegates all argument validation and script execution to native Slurm. `--help`, `--version`, `--test-only`, quiet submissions, stdin, arrays, heterogeneous arguments, script arguments and `--wrap` are not rewritten. A failing `--wait` job keeps its native exit code and is still tracked when an ID is observed. Tracking availability is not a prerequisite for submission. Quiet/unrecognized output and cluster-qualified IDs are retained as unresolved receipts rather than guessed into the local database. Terminal-sensitive plugins can use `CSLURM_TRACK_SUBMISSIONS=false csbatch ...` for direct native exec without tracking. See [compatibility and limitations](docs/sbatch-compatibility.md).

`csrun` passes every argument unchanged to real `srun`, including empty/invalid arguments for native validation. The CLI inherits stdin/stdout/stderr and inheritable file descriptors. Completion tracking is asynchronous; database locks and tracking errors do not change the native result. A step does not mark its parent allocation completed. Standalone executions receive local tracking IDs, not inferred Slurm IDs.

`cscancel` passes every argument unchanged, including interactive, filter, array, step and signal options. CleverSlurm's former `--note` is no longer intercepted; use `CSLURM_CANCEL_NOTE="reason" cscancel JOB_ID` for an intentional cancellation. An audit records requests, not confirmed cancellation. If renamed to `scancel`, broad commands retain their normal Slurm meaning and risks. No cancellation is retried by the receipt importer.

Both commands preserve native CLI output bytes and exit/signal status. Exact PID-sensitive or special terminal integrations can use `CSLURM_TRACK_COMMANDS=false csrun ...` (or `cscancel ...`) to exec native Slurm directly without tracking. See [srun/scancel compatibility and limits](docs/srun-scancel-compatibility.md).

For new batch jobs, completion is recorded from Slurm accounting by `ctrack`; no `PROGRAM_FINISHED` trap is injected. Existing runtime markers from earlier versions remain readable. Static command detection is best-effort: it does not trace every file opened by the application or infer domain-specific output files. Notification analysis scans recorded stdout/stderr paths and falls back to Slurm's default `slurm-<job_id>.out` under the submit directory for older records without recorded paths. This upgrade does not enqueue historical completion work or replay old notifications.

## Safety Notes

- SQLite must be local to the management host, not opened over NFS by compute nodes. `csrun` within allocations spools records for `ctrack` instead. See [database safety and recovery](docs/database-safety.md), including the required offline migration for existing WAL databases.
- `cscancel` passes through to Slurm `scancel`; there is no extra CleverSlurm batch-cancel helper, but broad real `scancel` options still do what Slurm normally does.
- Tests use fake Slurm commands and do not cancel real Slurm jobs.
- For real-cluster smoke tests, use a fresh `CSLURM_ROOT` so experiments do not modify an existing tracking database.
- Do not delete remote test directories automatically unless the owner explicitly asks.

## Development

Run the test suite:

```bash
python3 -m pytest -q
python3 -m compileall -q src/cslurm
```

The current suite covers fake `sbatch`, fake `srun`, fake `sacct`, fake `scancel`, SQLite writes, static MATLAB/Python submission analysis, runtime command ingestion, Julia include parsing, snapshots, AI request construction, AI question answering over job facts, notification analysis, Feishu immediate and grouped dispatch with fake HTTP, and query CLI helpers.

See [docs/testing.md](docs/testing.md), [docs/configuration.md](docs/configuration.md), [docs/notifications.md](docs/notifications.md), and [docs/feishu_setup.md](docs/feishu_setup.md).
