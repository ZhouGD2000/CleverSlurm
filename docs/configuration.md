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
export DEEPSEEK_API_KEY=...
export AI_SLURM_AI_PROVIDER=openai-compatible
export AI_SLURM_AI_BASE_URL=https://api.deepseek.com
export AI_SLURM_AI_MODEL=deepseek-v4-pro
export AI_SLURM_AI_MAX_TOKENS=512
export AI_SLURM_AI_TIMEOUT_SECONDS=30
export AI_SLURM_AI_FALLBACK_MODELS=deepseek-chat
```

Local config file:

```toml
[ai]
provider = "openai-compatible"
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
model = "deepseek-v4-pro"
max_tokens = "512"
response_format = "json_object"
auto_summary = "true"
timeout_seconds = "30"
fallback_models = "deepseek-chat"
```

Supported `provider` values:

- `openai-compatible`: POSTs to `<base_url>/chat/completions`.
- `anthropic-compatible`: POSTs to `<base_url>/v1/messages`.

Examples:

```toml
[ai]
provider = "openai-compatible"
api_key_env = "KIMI_API_KEY"
base_url = "https://api.kimi.com/coding/v1"
model = "kimi-for-coding"
max_tokens = "512"
```

```toml
[ai]
provider = "anthropic-compatible"
api_key_env = "KIMI_API_KEY"
base_url = "https://api.kimi.com/coding/"
model = "kimi-for-coding"
max_tokens = "512"
```

```toml
[ai]
provider = "anthropic-compatible"
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com/anthropic"
model = "deepseek-v4-pro"
max_tokens = "512"
```

`extra_body_json` can pass provider-specific fields to OpenAI-compatible or Anthropic-compatible APIs:

```toml
[ai]
extra_body_json = "{\"thinking\":{\"type\":\"enabled\"},\"reasoning_effort\":\"high\"}"
```

Set `response_format = "none"` if an OpenAI-compatible endpoint rejects `response_format`.

`enable_thinking` is sent only when it is present in config or `AI_SLURM_AI_ENABLE_THINKING` is set. If it is absent, CleverSlurm omits the field entirely because provider support varies. Set it explicitly for OpenAI-compatible models that support that exact field:

```toml
[ai]
enable_thinking = "true"
```

or, for SiliconFlow Qwen chat/json use where thinking should be disabled:

```toml
[ai]
enable_thinking = "false"
```

or:

```bash
aisummarize 46644 --enable-thinking
```

`timeout_seconds` controls the read timeout for each model attempt. `fallback_models` is a comma-separated list of models using the same provider protocol and base URL. The primary `model` is always tried first; fallback models are tried only after retryable failures such as timeouts, HTTP 429, or HTTP 5xx responses. HTTP 400/401 style request or authentication errors are not hidden by fallback.

For a SiliconFlow Qwen setup where `Qwen/Qwen3.5-4B` is the preferred model but may occasionally not return promptly, keep it as the primary model and add a same-provider fallback:

```toml
[ai]
provider = "openai-compatible"
base_url = "https://api.siliconflow.cn/v1"
model = "Qwen/Qwen3.5-4B"
fallback_models = "Qwen/Qwen2.5-7B-Instruct"
timeout_seconds = "12"
enable_thinking = "false"
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

This webhook sends to the Feishu chat where the custom bot is installed. It is usually a group notification path, not arbitrary personal direct messaging. Direct one-to-one messages require a separate Feishu app bot backend and are not implemented yet. See [Feishu Setup](feishu_setup.md) for Feishu-side setup steps and the app-bot requirements for personal delivery.

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

If you choose to store the values directly in `~/.ai-slurm/config.toml`, use:

```toml
[notification.feishu]
webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
secret = "..."
```

The `*_env` fields are environment variable names. Direct values are supported for convenience, but environment variables are safer for shared repositories and shell histories.

`aitrack` records a `job_analysis` row and a `notifications` row when a job reaches a terminal Slurm state. Hard failures, nonzero `ExitCode`, and nonzero `DerivedExitCode` are immediate notifications. Normal completions and user cancellations are grouped into batch or digest summary cards after `batch_window_minutes`.

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
ainotify dispatch --mode batch --force
ainotify dispatch --mode digest --force
ainotify dispatch --mode all
```

## Recommended Permissions

The config may contain an API key, so keep it private:

```bash
chmod 600 ~/.ai-slurm/config.toml
```

Never commit real API keys.
