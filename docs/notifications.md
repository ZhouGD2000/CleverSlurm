# Notifications

CleverSlurm records every terminal job transition before deciding whether to push a message. The current implementation follows the reference guide's first working slice:

- deterministic Slurm state and exit-code classification
- keyword/regex scanning of recorded stdout and stderr
- simple user-defined success criteria checks
- optional AI semantic log analysis over compact log packets
- notification queue rows with dedupe keys
- immediate Feishu card dispatch for hard failures
- grouped batch/digest Feishu cards for normal completions and user cancellations

## Flow

```text
aitrack
  -> update jobs from sacct
  -> ingest runtime finish/command logs
  -> analyze terminal jobs
  -> optionally run AI semantic log analysis
  -> insert job_analysis
  -> insert notifications
  -> dispatch immediate Feishu notifications
  -> flush due batch/digest summaries
```

Hard failure states are `FAILED`, `OUT_OF_MEMORY`, `TIMEOUT`, `NODE_FAIL`, `BOOT_FAIL`, `DEADLINE`, `REVOKED`, and `SPECIAL_EXIT`. Nonzero `ExitCode` or `DerivedExitCode` is also treated as a hard failure.

`CANCELLED` with a prior `aiscancel --note ...` or other cancellation event is recorded as user cancellation and assigned digest mode.

## Success Criteria

Set criteria with `AI_SLURM_SUCCESS_CRITERIA` or place `.ai-slurm.yml` in the submit directory:

```yaml
success_criteria:
  require_output_files:
    - "results/*.jld2"
  log_must_contain:
    - "converged = true"
  log_must_not_contain:
    - "NaN"
    - "Diverged"
```

If a completed job misses a required file or violates a log rule, CleverSlurm records `semantic_failed` with category `SUCCESS_CRITERIA_NOT_MET`.

## Feishu Setup

Configure a Feishu/Lark custom bot webhook with environment variables:

```bash
export AI_SLURM_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export AI_SLURM_FEISHU_SECRET="..."
```

If the bot does not use signing, omit `AI_SLURM_FEISHU_SECRET`. If the bot uses keyword validation, make sure the keyword accepts `AI-Slurm`, which is included in generated titles.

The current backend sends to the Feishu chat where the custom bot webhook is installed. It cannot choose an arbitrary personal recipient at send time. For one-person delivery with the current code, use a private chat/group that contains only the intended recipient and the custom bot, if your Feishu tenant permits that. True direct messages require a Feishu app bot backend with app ID, app secret, recipient IDs, token handling, and the IM send-message API.

See [Feishu Setup](feishu_setup.md) for the Feishu-side steps, security settings, and the difference between custom webhooks and app-bot direct messages.

Optional `~/.ai-slurm/config.toml`:

```toml
[notification]
enabled = "true"
auto_dispatch = "true"

[notification.feishu]
webhook_url_env = "AI_SLURM_FEISHU_WEBHOOK"
secret_env = "AI_SLURM_FEISHU_SECRET"
message_format = "card"
batch_window_minutes = "30"
```

## Commands

Inspect notification rows:

```bash
aijobs notifications
aijobs notifications 46644
```

Manually dispatch pending notifications:

```bash
ainotify pending
ainotify dispatch
ainotify dispatch --mode batch --force
ainotify dispatch --mode digest --force
ainotify dispatch --mode all
```

`aitrack` dispatches automatically by default. Immediate notifications are sent as individual cards. `batch` and `digest` notifications are grouped by `group_id` when available, otherwise by category, and sent as summary cards once the configured window has elapsed. Set `AI_SLURM_NOTIFICATION_AUTO_DISPATCH=false` to record rows without sending from the tracker.

`ainotify dispatch --mode batch --force` is useful for testing because it sends the grouped summary without waiting for `batch_window_minutes`.

Enable AI semantic log analysis for completion notifications:

```bash
export AI_SLURM_NOTIFICATION_AI_ANALYSIS=true
```

The AI receives compact stdout/stderr head/tail snippets, matched evidence windows, runtime command metadata, and factual Slurm fields. Its output can refine semantic status for non-hard-failed jobs, but it does not override Slurm state, `ExitCode`, `DerivedExitCode`, or deterministic hard-failure classification.

## Current Limits

Large scan grouping currently uses `group_id` when it is present and otherwise falls back to category. Daily digest scheduling is not built in; run `ainotify dispatch --mode digest` from cron or another scheduler if you want a fixed wall-clock digest.
