# Configuration

CleverSlurm reads configuration from environment variables first, then from `~/.ai-slurm/config.toml`, then from built-in defaults.

## Storage

Default root:

```text
~/.ai-slurm
```

Override it for tests or isolated deployments:

```bash
export AI_SLURM_ROOT=/path/to/ai-slurm-root
```

The root contains:

```text
db.sqlite
jobs/<job_id>/
store/sha256/
config.toml
```

## Slurm Command Paths

By default, commands are resolved from `PATH`.

Override command paths with environment variables:

```bash
export AI_SLURM_SBATCH=/usr/bin/sbatch
export AI_SLURM_SRUN=/usr/bin/srun
export AI_SLURM_SACCT=/usr/bin/sacct
export AI_SLURM_SCANCEL=/usr/bin/scancel
```

These overrides are also how tests inject fake Slurm commands.

## AI Settings

Environment variables:

```bash
export SILICONFLOW_API_KEY=...
export AI_SLURM_AI_MODEL=Qwen/Qwen3.5-4B
export AI_SLURM_AI_MAX_TOKENS=512
```

Local config file:

```toml
[ai]
api_key = "..."
model = "Qwen/Qwen3.5-4B"
max_tokens = "512"
auto_summary = "true"
```

`enable_thinking` is not sent by default because some SiliconFlow models reject it. Enable it only for models that support it:

```toml
[ai]
enable_thinking = "true"
```

or:

```bash
aisummarize 46644 --enable-thinking
```

The same AI configuration is used by:

```bash
aijobs ask "最近完成了什么任务？都是些什么工作？"
```

`aijobs ask` builds a JSON context from recent SQLite job records, then asks the configured model to answer from that context only.

`aisbatch` triggers a submission summary automatically after it records a successful submission. If the AI request fails, the job submission still succeeds and an `AI_SUMMARY_FAILED` event is recorded. Disable automatic summaries when needed:

```bash
export AI_SLURM_AI_AUTO_SUMMARY=false
```

or:

```toml
[ai]
auto_summary = "false"
```

## Feishu Notifications

Feishu notifications use a Feishu/Lark custom bot webhook. Keep the webhook and signing secret in environment variables:

```bash
export AI_SLURM_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export AI_SLURM_FEISHU_SECRET="..."
```

Optional config:

```toml
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

`aitrack` records a `job_analysis` row and a `notifications` row when a job reaches a terminal Slurm state. Hard failures, nonzero `ExitCode`, and nonzero `DerivedExitCode` are immediate notifications. Normal completions and user cancellations are recorded for batch or digest handling.

AI semantic log analysis is opt-in:

```bash
export AI_SLURM_NOTIFICATION_AI_ANALYSIS=true
```

When enabled, the AI receives compact log snippets and deterministic facts. Its output may refine semantic status for completed jobs, but it cannot override factual Slurm state or hard-failure classification.

Useful commands:

```bash
aijobs notifications
aijobs notifications 46644
ainotify pending
ainotify dispatch
```

## Recommended Permissions

The config may contain an API key, so keep it private:

```bash
chmod 600 ~/.ai-slurm/config.toml
```

Never commit real API keys.
