# Configuration

CleverSlurm reads configuration from environment variables first, then from `~/.cslurm/config.toml`, then from built-in defaults.

## Storage

Default root:

```text
~/.cslurm
```

Override it for tests or isolated deployments:

```bash
export CSLURM_ROOT=/path/to/cslurm-root
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
export CSLURM_SBATCH=/usr/bin/sbatch
export CSLURM_SRUN=/usr/bin/srun
export CSLURM_SACCT=/usr/bin/sacct
export CSLURM_SCANCEL=/usr/bin/scancel
```

These overrides are also how tests inject fake Slurm commands.

## AI Settings

Environment variables:

```bash
export DEEPSEEK_API_KEY=...
export CSLURM_AI_FORMAT=openai
export CSLURM_AI_BASE_URL=https://api.deepseek.com
export CSLURM_AI_MODEL=deepseek-v4-pro
export CSLURM_AI_MAX_TOKENS=512
export CSLURM_AI_TIMEOUT_SECONDS=30
export CSLURM_AI_FALLBACK_MODELS=deepseek-chat
```

Local config file:

```toml
[ai]
format = "openai"
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
model = "deepseek-v4-pro"
max_tokens = "512"
auto_summary = "true"
timeout_seconds = "30"
request_retries = "1"
fallback_models = "deepseek-chat"
```

Supported `format` values:

- `openai`: POSTs to `<base_url>/chat/completions`.
- `anthropic`: POSTs to `<base_url>/v1/messages`.

Examples:

```toml
[ai]
format = "openai"
api_key_env = "KIMI_API_KEY"
base_url = "https://api.kimi.com/coding/v1"
model = "kimi-for-coding"
max_tokens = "512"
```

Some Kimi Code API keys are accepted by the Anthropic-compatible endpoint but rejected by the OpenAI-compatible endpoint with an HTTP 403 provider-policy error. Use the Anthropic-compatible form below unless your Kimi account explicitly enables the OpenAI-compatible path for that key.

```toml
[ai]
format = "anthropic"
api_key_env = "KIMI_API_KEY"
base_url = "https://api.kimi.com/coding/"
model = "kimi-for-coding"
max_tokens = "512"
```

```toml
[ai]
format = "anthropic"
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

CleverSlurm does not send `response_format` by default because provider support differs, and some models can become slow or fail when JSON mode is forced. The prompts still ask for one JSON object, and CleverSlurm extracts that object even when a provider wraps it in Markdown fences or short surrounding text.

If a provider returns invalid JSON or times out on a structured response, the AI-facing commands fall back to a shorter natural-language prompt and store the model's text inside a structured local record. This keeps `csbatch` automatic summaries, `csummarize`, `cjobs ask`, and notification AI analysis usable on smaller or less JSON-stable models.

For an OpenAI-compatible endpoint that benefits from JSON mode, enable it explicitly:

```toml
[ai]
response_format = "json_object"
```

Set `response_format = "none"` to force the field off even when an environment override or copied config would otherwise set it.

`enable_thinking` is sent only when it is present in config or `CSLURM_AI_ENABLE_THINKING` is set. If it is absent, CleverSlurm omits the field entirely because provider support varies. Set it explicitly for OpenAI-compatible models that support that exact field:

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
csummarize 46644 --enable-thinking
```

`timeout_seconds` controls the read timeout for each model attempt. `request_retries` controls same-model retries for transport-level errors such as a transient SSL EOF; timeouts still move directly to fallback to avoid doubling waits on slow models.

`fallback_models` is a comma-separated list of models using the same provider protocol and base URL. The primary `model` is always tried first; fallback models are tried after retryable failures such as timeouts, HTTP 429, HTTP 5xx responses, empty responses, or model output that does not contain a JSON object. HTTP 400/401 style request or authentication errors are not hidden by fallback.

For a SiliconFlow Qwen setup where `Qwen/Qwen3.5-4B` is the preferred model but may occasionally not return promptly, keep it as the primary model and add a same-provider fallback:

```toml
[ai]
format = "openai"
base_url = "https://api.siliconflow.cn/v1"
model = "Qwen/Qwen3.5-4B"
fallback_models = "Qwen/Qwen2.5-7B-Instruct"
timeout_seconds = "12"
enable_thinking = "false"
response_format = "none"
```

The same AI configuration is used by:

```bash
cjobs ask "最近完成了什么任务？都是些什么工作？"
```

`cjobs ask` builds a JSON context from recent SQLite job records, then asks the configured model to answer from that context only.

`csbatch` triggers a submission summary automatically after it records a successful submission. If the AI request fails, the job submission still succeeds and an `AI_SUMMARY_FAILED` event is recorded. Disable automatic summaries when needed:

```bash
export CSLURM_AI_AUTO_SUMMARY=false
```

or:

```toml
[ai]
auto_summary = "false"
```

## Feishu Notifications

Feishu notifications use a Feishu/Lark custom bot webhook. Keep the webhook and signing secret in environment variables:

```bash
export CSLURM_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export CSLURM_FEISHU_SECRET="..."
```

This webhook sends to the Feishu chat where the custom bot is installed. It is usually a group notification path, not arbitrary personal direct messaging. Direct one-to-one messages require a separate Feishu app bot backend and are not implemented yet. See [Feishu Setup](feishu_setup.md) for Feishu-side setup steps and the app-bot requirements for personal delivery.

Optional config:

```toml
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

If you choose to store the values directly in `~/.cslurm/config.toml`, use:

```toml
[notification.feishu]
webhook_url = "https://open.feishu.cn/open-apis/bot/v2/hook/..."
secret = "..."
```

The `*_env` fields are environment variable names. Direct values are supported for convenience, but environment variables are safer for shared repositories and shell histories.

`ctrack` records a `job_analysis` row and a `notifications` row when a job reaches a terminal Slurm state. Hard failures, nonzero `ExitCode`, and nonzero `DerivedExitCode` are immediate notifications. Normal completions and user cancellations are grouped into batch or digest summary cards after `batch_window_minutes`.

If many immediate notifications are pending at once, CleverSlurm groups them into summary cards once the count reaches `immediate_group_threshold` instead of sending one Feishu message per job. The default threshold is `10`; set it to `1` to always group immediate notifications, or higher to keep small bursts as individual cards:

```bash
export CSLURM_NOTIFICATION_IMMEDIATE_GROUP_THRESHOLD=10
```

or:

```toml
[notification.feishu]
immediate_group_threshold = "10"
```

AI semantic log analysis is opt-in:

```bash
export CSLURM_NOTIFICATION_AI_ANALYSIS=true
```

When enabled, the AI receives compact log snippets and deterministic facts. Its output may refine semantic status for completed jobs, but it cannot override factual Slurm state or hard-failure classification.

Useful commands:

```bash
cjobs notifications
cjobs notifications 46644
cnotify pending
cnotify dispatch
cnotify dispatch --mode batch --force
cnotify dispatch --mode digest --force
cnotify dispatch --mode all
```

## Recommended Permissions

The config may contain an API key, so keep it private:

```bash
chmod 600 ~/.cslurm/config.toml
```

Never commit real API keys.
