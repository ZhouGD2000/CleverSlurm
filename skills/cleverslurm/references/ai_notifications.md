# CleverSlurm AI And Notifications

## AI Providers

CleverSlurm supports two request formats:

- `format = "openai"`: POSTs to `<base_url>/chat/completions`.
- `format = "anthropic"`: POSTs to `<base_url>/v1/messages`.

Configuration lives in `~/.cslurm/config.toml` or environment variables. Prefer environment variables for secrets:

```toml
[ai]
format = "openai"
api_key_env = "DEEPSEEK_API_KEY"
base_url = "https://api.deepseek.com"
model = "deepseek-chat"
max_tokens = "512"
timeout_seconds = "60"
request_retries = "1"
auto_summary = "true"
```

Provider-specific fields can go through `extra_body_json`. `enable_thinking` is only sent when configured.

## Summaries

`csbatch` queues an automatic submission summary in a detached worker when AI auto-summary is enabled. Manual commands:

```bash
csummarize JOBID
csummarize JOBID --completion
cjobs summary JOBID
cjobs summary JOBID --completion
```

`cjobs ask` asks natural-language questions over recorded facts and falls back to deterministic local answers if model calls fail.

## Feishu/Lark Notifications

Feishu custom bot webhook configuration:

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

`ctrack` creates notification records when jobs enter terminal states. It dispatches due notifications when `auto_dispatch` is enabled.

Use:

```bash
cjobs notifications JOBID
cnotify dispatch --mode all
```

Never put webhook URLs, secrets, or API keys in committed files.
