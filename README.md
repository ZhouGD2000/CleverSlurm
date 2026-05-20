# CleverSlurm

CleverSlurm is a small, deterministic Slurm job tracking tool. It wraps common Slurm commands, records what was submitted and what happened, and stores job facts in SQLite so they can be queried later.

The design rule is simple: deterministic code records facts; AI only summarizes recorded facts.

## Current Features

- `aisbatch`: wraps `sbatch --parsable`, passes through normal sbatch options, supports script submissions and basic `--wrap`, copies the original or generated script, writes an instrumented script, records job metadata, git status/diff, stdout/stderr paths, submission events, and a program-finished runtime marker.
- `aisrun`: wraps direct `srun` execution, passes stdout/stderr through in CLI mode, and records a standalone local job record when outside an existing allocation.
- `aiscancel`: wraps `scancel`, passes through scancel options, and records a cancellation request event with an optional CleverSlurm-only `--note`.
- `aitrack`: polls `sacct` for known jobs and updates state, exit code, derived exit code, elapsed time, memory, and node list.
- `aijobs`: queries recent jobs, job details, events, files, runtime commands, log tails, and AI answers over recent job facts.
- `aisummarize`: sends curated job facts to a configured OpenAI-compatible or Anthropic-compatible model API and stores structured AI summaries.
- Feishu notifications: when `aitrack` sees a terminal job state, it records deterministic/semantic analysis, queues a notification, and immediately sends hard failures through a Feishu/Lark custom bot when configured.
- Runtime command ingestion: imports JSONL records from `commands.log` into `job_commands`.
- Local fake-Slurm tests: the test suite does not require Slurm.

## Install

From the repository root:

```bash
python3 -m pip install -e .
```

For development without installing console scripts, run with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python3 -m ai_slurm.cli.aisbatch job.slurm
```

## Configuration

By default CleverSlurm writes to:

```text
~/.ai-slurm/
  db.sqlite
  jobs/
  store/
  config.toml
```

Use `AI_SLURM_ROOT` to isolate a test database:

```bash
AI_SLURM_ROOT=/tmp/cleverslurm-test aisbatch job.slurm
```

Example `~/.ai-slurm/config.toml`:

```toml
[ai]
provider = "openai-compatible"
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
model = "deepseek-v4-pro"
max_tokens = "512"
response_format = "json_object"
auto_summary = "true"

[notification]
enabled = "true"
auto_dispatch = "true"
ai_analysis = "false"

[notification.feishu]
webhook_url_env = "AI_SLURM_FEISHU_WEBHOOK"
secret_env = "AI_SLURM_FEISHU_SECRET"
message_format = "card"
batch_window_minutes = "30"
```

Do not commit API keys. Put them in environment variables such as `DEEPSEEK_API_KEY` or `KIMI_API_KEY`.

Enable Feishu without writing secrets to disk:

```bash
export AI_SLURM_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export AI_SLURM_FEISHU_SECRET="..."
```

## Basic Usage

Submit a batch script:

```bash
aisbatch job.slurm
aisbatch -p CPU2 --time=00:01:00 job.slurm arg1 arg2
aisbatch -p CPU2 --wrap "hostname && python3 script.py"
```

After a successful submission, `aisbatch` automatically triggers an AI submission summary and stores it in `summary_json`. If the AI request fails, submission still succeeds and `aijobs events <job_id>` will show `AI_SUMMARY_FAILED`.

Track known jobs:

```bash
aitrack
```

Inspect jobs:

```bash
aijobs recent
aijobs show 46644
aijobs events 46644
aijobs files 46644
aijobs commands 46644
aijobs logs 46644 --tail 100
aijobs notifications 46644
aijobs ask "最近完成了什么任务？都是些什么工作？"
```

Run a direct Slurm command:

```bash
aisrun python script.py
```

Summarize a job with AI:

```bash
aisummarize 46644
aisummarize 46644 --completion
```

Temporarily override the AI model:

```bash
aisummarize 46644 --model deepseek-v4-pro --max-tokens 512
```

Ask AI about recent jobs:

```bash
aijobs ask "最近完成了什么任务？都是些什么工作？"
aijobs ask "最近失败的任务有哪些，原因是什么？" -n 20
```

`aijobs ask` sends only recorded database facts to the model. It does not ask AI to infer Slurm job ids, states, exit codes, paths, or commands from memory.

Dispatch pending Feishu notifications manually:

```bash
ainotify pending
ainotify dispatch
ainotify dispatch --mode batch --force
ainotify dispatch --mode digest --force
ainotify dispatch --mode all
```

`aitrack` normally dispatches immediate notifications automatically after it records a terminal state. It also flushes due batch/digest summaries according to `batch_window_minutes`; `--force` is only needed when you want to send a summary before the window expires.

## Support Boundaries

`aisbatch` preserves leading `#SBATCH` directives and passes most normal sbatch command-line options to real `sbatch`. Basic `--wrap` is translated to an instrumented temporary script. Some unusual sbatch forms may still need explicit testing before replacing `sbatch` cluster-wide.

`aisrun` passes arguments to real `srun`. In CLI mode it lets the child process inherit stdout/stderr, so it behaves more like `srun` for normal terminal use. Interactive PTY-heavy workflows such as `srun --pty bash` should still be smoke-tested on the target cluster before relying on a rename.

`aiscancel` passes arguments to real `scancel` after removing CleverSlurm's optional `--note`. If you rename it to `scancel`, broad scancel commands keep their normal Slurm meaning; use the same caution you would use with real `scancel`.

For batch jobs, CleverSlurm records submission immediately. The instrumented script also writes a runtime `PROGRAM_FINISHED` marker when the batch script exits. Run `aitrack` to ingest runtime markers and Slurm accounting state into SQLite.

## Safety Notes

- `aiscancel` passes through to Slurm `scancel`; there is no extra CleverSlurm batch-cancel helper, but broad real `scancel` options still do what Slurm normally does.
- Tests use fake Slurm commands and do not cancel real Slurm jobs.
- For real-cluster smoke tests, use a fresh `AI_SLURM_ROOT` so experiments do not modify an existing tracking database.
- Do not delete remote test directories automatically unless the owner explicitly asks.

## Development

Run the test suite:

```bash
python3 -m pytest -q
python3 -m compileall -q src/ai_slurm
```

The current suite covers fake `sbatch`, fake `srun`, fake `sacct`, fake `scancel`, SQLite writes, runtime command ingestion, Julia include parsing, snapshots, AI request construction, AI question answering over job facts, notification analysis, Feishu immediate and grouped dispatch with fake HTTP, and query CLI helpers.

See [docs/testing.md](docs/testing.md), [docs/configuration.md](docs/configuration.md), and [docs/notifications.md](docs/notifications.md).
