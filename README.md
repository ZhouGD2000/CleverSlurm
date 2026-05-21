# CleverSlurm

CleverSlurm is a small, deterministic Slurm job tracking tool. It wraps common Slurm commands, records what was submitted and what happened, and stores job facts in SQLite so they can be queried later.

The design rule is simple: deterministic code records facts; AI only summarizes recorded facts.

## Current Features

- `csbatch`: wraps `sbatch --parsable`, passes through normal sbatch options, supports script submissions and basic `--wrap`, copies the original or generated script, writes an instrumented script, records job metadata, git status/diff, stdout/stderr paths, submission events, and a program-finished runtime marker.
- `csrun`: wraps direct `srun` execution, passes stdout/stderr through in CLI mode, and records a standalone local job record when outside an existing allocation.
- `cscancel`: wraps `scancel`, passes through scancel options, and records a cancellation request event with an optional CleverSlurm-only `--note`.
- `ctrack`: polls `sacct` for known jobs and updates state, exit code, derived exit code, elapsed time, memory, and node list.
- `cjobs`: queries recent jobs, job details, events, files, runtime commands, log tails, and AI answers over recent job facts.
- `csummarize`: sends curated job facts to a configured OpenAI-compatible or Anthropic-compatible model API and stores structured AI summaries.
- Feishu notifications: when `ctrack` sees a terminal job state, it records deterministic/semantic analysis, queues a notification, and immediately sends hard failures through a Feishu/Lark custom bot when configured.
- Runtime command ingestion: imports JSONL records from `commands.log` into `job_commands`.
- Local fake-Slurm tests: the test suite does not require Slurm.

## Install

From the repository root:

```bash
python3 -m pip install -e .
```

Uninstall the editable package and console scripts:

```bash
python3 -m pip uninstall CleverSlurm
```

This removes the installed Python package entry and command wrappers such as `csbatch`, `csrun`, `cjobs`, and `cnotify` from that Python environment. It does not remove runtime data or config under `~/.cslurm/`. Remove that directory separately only when you intentionally want to delete the local job database, copied scripts, logs, and secrets/config.

For development without installing console scripts, run with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 -m cslurm.cli.csbatch job.slurm
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
```

Do not commit API keys. Put them in environment variables such as `DEEPSEEK_API_KEY` or `KIMI_API_KEY`.

Enable Feishu without writing secrets to disk:

```bash
export CSLURM_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export CSLURM_FEISHU_SECRET="..."
```

## Basic Usage

Submit a batch script:

```bash
csbatch job.slurm
csbatch -p CPU2 --time=00:01:00 job.slurm arg1 arg2
csbatch -p CPU2 --wrap "hostname && python3 script.py"
```

After a successful submission, `csbatch` queues the AI submission summary in a detached background worker and returns immediately. `cjobs events <job_id>` first shows `AI_SUMMARY_QUEUED`; when the worker finishes it records `AI_SUMMARY_CREATED` and stores `summary_json`, or records `AI_SUMMARY_FAILED` if the request fails. Worker stdout/stderr goes to `~/.cslurm/jobs/<job_id>/auto_summary.log`.

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

The generated cron entry uses `flock`, runs once per minute, writes logs to `~/.cslurm/ctrack.log`, and only edits the marked CleverSlurm block in the user's crontab.

Inspect jobs:

```bash
cjobs recent
cjobs show 46644
cjobs events 46644
cjobs files 46644
cjobs commands 46644
cjobs logs 46644 --tail 100
cjobs notifications 46644
cjobs ask "最近完成了什么任务？都是些什么工作？"
```

Run a direct Slurm command:

```bash
csrun python script.py
```

Summarize a job with AI:

```bash
csummarize 46644
csummarize 46644 --completion
```

Temporarily override the AI model:

```bash
csummarize 46644 --model deepseek-v4-pro --max-tokens 512
```

Ask AI about recent jobs:

```bash
cjobs ask "最近完成了什么任务？都是些什么工作？"
cjobs ask "最近失败的任务有哪些，原因是什么？" -n 20
```

`cjobs ask` sends only recorded database facts to the model. It does not ask AI to infer Slurm job ids, states, exit codes, paths, or commands from memory.

Dispatch pending Feishu notifications manually:

```bash
cnotify pending
cnotify dispatch
cnotify dispatch --mode batch --force
cnotify dispatch --mode digest --force
cnotify dispatch --mode all
```

`ctrack` normally dispatches immediate notifications automatically after it records a terminal state. It also flushes due batch/digest summaries according to `batch_window_minutes`; `--force` is only needed when you want to send a summary before the window expires.

## Support Boundaries

`csbatch` preserves leading `#SBATCH` directives and passes most normal sbatch command-line options to real `sbatch`. Basic `--wrap` is translated to an instrumented temporary script. Some unusual sbatch forms may still need explicit testing before replacing `sbatch` cluster-wide.

`csrun` passes arguments to real `srun`. In CLI mode it lets the child process inherit stdout/stderr, so it behaves more like `srun` for normal terminal use. Interactive PTY-heavy workflows such as `srun --pty bash` should still be smoke-tested on the target cluster before relying on a rename.

`cscancel` passes arguments to real `scancel` after removing CleverSlurm's optional `--note`. If you rename it to `scancel`, broad scancel commands keep their normal Slurm meaning; use the same caution you would use with real `scancel`.

For batch jobs, CleverSlurm records submission immediately. The instrumented script also writes a runtime `PROGRAM_FINISHED` marker when the batch script exits. Run `ctrack` to ingest runtime markers and Slurm accounting state into SQLite.

## Safety Notes

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

The current suite covers fake `sbatch`, fake `srun`, fake `sacct`, fake `scancel`, SQLite writes, runtime command ingestion, Julia include parsing, snapshots, AI request construction, AI question answering over job facts, notification analysis, Feishu immediate and grouped dispatch with fake HTTP, and query CLI helpers.

See [docs/testing.md](docs/testing.md), [docs/configuration.md](docs/configuration.md), [docs/notifications.md](docs/notifications.md), and [docs/feishu_setup.md](docs/feishu_setup.md).
