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
ctrack
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

`CANCELLED` with a prior `cscancel --note ...` or other cancellation event is recorded as user cancellation and assigned digest mode.

## Success Criteria

Set criteria with `CSLURM_SUCCESS_CRITERIA` or place `.cslurm.yml` in the submit directory:

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
export CSLURM_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export CSLURM_FEISHU_SECRET="..."
```

If the bot does not use signing, omit `CSLURM_FEISHU_SECRET`. If the bot uses keyword validation, make sure the keyword accepts `CleverSlurm`, which is included in generated titles.

The current backend sends to the Feishu chat where the custom bot webhook is installed. It cannot choose an arbitrary personal recipient at send time. For one-person delivery with the current code, use a private chat/group that contains only the intended recipient and the custom bot, if your Feishu tenant permits that. True direct messages require a Feishu app bot backend with app ID, app secret, recipient IDs, token handling, and the IM send-message API.

See [Feishu Setup](feishu_setup.md) for the Feishu-side steps, security settings, and the difference between custom webhooks and app-bot direct messages.

Optional `~/.cslurm/config.toml`:

```toml
[notification]
enabled = "true"
auto_dispatch = "true"

[notification.feishu]
webhook_url_env = "CSLURM_FEISHU_WEBHOOK"
secret_env = "CSLURM_FEISHU_SECRET"
message_format = "card"
batch_window_minutes = "30"
immediate_group_threshold = "10"
```

## Commands

Inspect notification rows:

```bash
cjobs notifications
cjobs notifications 46644
```

Manually dispatch pending notifications:

```bash
cnotify pending
cnotify dispatch
cnotify dispatch --mode batch --force
cnotify dispatch --mode digest --force
cnotify dispatch --mode all
```

`ctrack` dispatches automatically by default and processes up to 500 pending notifications per run. A small number of immediate hard-failure notifications are sent as individual cards. If pending immediate notifications reach `immediate_group_threshold`, they are grouped by `group_id` when available, otherwise by category, and sent as summary cards. `batch` and `digest` notifications use the same grouping strategy and are sent once the configured window has elapsed. Set `CSLURM_NOTIFICATION_AUTO_DISPATCH=false` to record rows without sending from the tracker.

`cnotify dispatch --mode batch --force` is useful for testing because it sends the grouped summary without waiting for `batch_window_minutes`.

For automatic status refresh and notification dispatch, install a cron entry on the Slurm login/management host:

```cron
* * * * * cd /home/zgd/software/cleverslurm && /usr/bin/flock -n $HOME/.cslurm/ctrack.lock env CSLURM_ROOT=$HOME/.cslurm PYTHONPATH=/home/zgd/software/cleverslurm/src python3 -m cslurm.cli.ctrack >> $HOME/.cslurm/ctrack.log 2>&1
```

The `flock` guard avoids overlapping tracker runs if Slurm accounting or Feishu is slow.

Enable AI semantic log analysis for completion notifications:

```bash
export CSLURM_NOTIFICATION_AI_ANALYSIS=true
```

The AI receives compact stdout/stderr head/tail snippets, matched evidence windows, runtime command metadata, and factual Slurm fields. Its output can refine semantic status for non-hard-failed jobs, but it does not override Slurm state, `ExitCode`, `DerivedExitCode`, or deterministic hard-failure classification.

## Current Limits

Large scan grouping currently uses `group_id` when it is present and otherwise falls back to category. Daily digest scheduling is not built in; run `cnotify dispatch --mode digest` from cron or another scheduler if you want a fixed wall-clock digest.
