# CleverSlurm

CleverSlurm is a small, deterministic Slurm job tracking tool. It wraps common Slurm commands, records what was submitted and what happened, and stores job facts in SQLite so they can be queried later.

The design rule is simple: deterministic code records facts; AI only summarizes recorded facts.

## Current Features

- `aisbatch`: wraps `sbatch --parsable`, copies the original Slurm script, writes an instrumented script, records job metadata, git status/diff, stdout/stderr paths, and submission events.
- `aisrun`: wraps direct `srun` execution and records a standalone local job record when outside an existing allocation.
- `aiscancel`: wraps `scancel` for one job and records a cancellation request event with an optional note.
- `aitrack`: polls `sacct` for known jobs and updates state, exit code, elapsed time, memory, and node list.
- `aijobs`: queries recent jobs, job details, events, files, runtime commands, log tails, and AI answers over recent job facts.
- `aisummarize`: sends curated job facts to SiliconFlow and stores structured AI summaries.
- Runtime command ingestion: imports JSONL records from `commands.log` into `job_commands`.
- Local fake-Slurm tests: the test suite does not require Slurm.

## Install

From the repository root:

```bash
python3 -m pip install -e .
```

For development without installing console scripts, run with `PYTHONPATH=.`:

```bash
PYTHONPATH=. python3 -m ai_slurm.cli.aisbatch job.slurm
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
api_key = "..."
model = "Qwen/Qwen3.5-4B"
max_tokens = "512"
```

Do not commit API keys.

## Basic Usage

Submit a batch script:

```bash
aisbatch job.slurm
```

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
aisummarize 46644 --model Qwen/Qwen3.5-4B --max-tokens 512
```

Ask AI about recent jobs:

```bash
aijobs ask "最近完成了什么任务？都是些什么工作？"
aijobs ask "最近失败的任务有哪些，原因是什么？" -n 20
```

`aijobs ask` sends only recorded database facts to the model. It does not ask AI to infer Slurm job ids, states, exit codes, paths, or commands from memory.

## Safety Notes

- `aiscancel` only cancels the job id you pass. There is no batch-cancel command in this repository.
- Tests use fake Slurm commands and do not cancel real Slurm jobs.
- For real-cluster smoke tests, use a fresh `AI_SLURM_ROOT` so experiments do not modify an existing tracking database.
- Do not delete remote test directories automatically unless the owner explicitly asks.

## Development

Run the test suite:

```bash
python3 -m pytest -q
python3 -m compileall -q ai_slurm
```

The current suite covers fake `sbatch`, fake `srun`, fake `sacct`, fake `scancel`, SQLite writes, runtime command ingestion, Julia include parsing, snapshots, AI request construction, AI question answering over job facts, and query CLI helpers.

See [docs/testing.md](docs/testing.md) and [docs/configuration.md](docs/configuration.md).
