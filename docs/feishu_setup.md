# Feishu Setup

CleverSlurm currently supports Feishu/Lark notifications through a custom bot webhook.

This is the lightest Feishu integration: no Feishu app backend is required, and the
cluster only needs to know the webhook URL plus the optional signing secret.

## Group vs Personal Delivery

The current webhook backend sends to the chat where the custom bot was installed.
It cannot choose an arbitrary recipient at send time.

Practical consequences:

- One webhook maps to one Feishu chat.
- Messages go to that chat, usually a group chat.
- To notify only yourself with the current implementation, create a private
  Feishu chat/group for yourself and add the custom bot there if your tenant
  allows it.
- Direct one-to-one delivery to a user by `open_id`, `user_id`, or email is a
  different Feishu integration path and is not implemented yet.

Direct personal messages require a Feishu app bot backend rather than a custom
bot webhook. That backend would need an app ID, app secret, a
`tenant_access_token`, bot message permissions, recipient IDs, and calls to the
Feishu IM send-message API.

This is why tools such as `claude-to-im` can be configured with only app ID and
app secret: they use the Feishu application bot model. CleverSlurm currently uses
custom bot webhooks because they are simpler for one-way cluster notifications
and do not require publishing or maintaining an app backend.

## Create A Custom Bot

In Feishu:

1. Open the Feishu chat that should receive CleverSlurm notifications.
2. Open chat settings.
3. Open the bot management page, usually named `群机器人` or `Bots`.
4. Add a custom bot.
5. Give it a recognizable name, for example `AI-Slurm`.
6. Copy the generated webhook URL.

The webhook usually looks like:

```text
https://open.feishu.cn/open-apis/bot/v2/hook/...
```

For Lark international tenants, the host may be:

```text
https://open.larksuite.com/open-apis/bot/v2/hook/...
```

Do not commit this URL. Treat it like a secret because anyone who has it may be
able to send messages through the bot, depending on the bot security settings.

## Security Settings

Feishu custom bots can be protected with one or more security checks. Use at
least one in real deployments.

Keyword validation:

- Add `AI-Slurm` as an allowed keyword.
- CleverSlurm generated card titles include `AI-Slurm`, so keyword validation can
  pass without changing code.

Signature validation:

- Enable signing in the custom bot settings.
- Copy the signing secret.
- Put the secret in `AI_SLURM_FEISHU_SECRET`.
- CleverSlurm will generate the timestamp/signature fields for each request.

IP allowlist:

- Add the public egress IP of the host that runs `aitrack` or `ainotify`.
- This can be awkward on laptops or NATed clusters where the egress IP changes.

## CleverSlurm Configuration

Set secrets through environment variables:

```bash
export AI_SLURM_FEISHU_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/..."
export AI_SLURM_FEISHU_SECRET="..."
```

If the bot does not use signature validation, omit `AI_SLURM_FEISHU_SECRET`.

Optional `~/.ai-slurm/config.toml`:

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

Useful environment overrides:

```bash
export AI_SLURM_NOTIFICATION_ENABLED=true
export AI_SLURM_NOTIFICATION_AUTO_DISPATCH=true
export AI_SLURM_NOTIFICATION_AI_ANALYSIS=false
```

## Test Delivery

After a tracked job reaches a terminal state, inspect queued notifications:

```bash
aijobs notifications
ainotify pending
```

Send pending immediate notifications and any due grouped notifications:

```bash
ainotify dispatch --mode all
```

Force grouped batch/digest summaries without waiting for `batch_window_minutes`:

```bash
ainotify dispatch --mode batch --force
ainotify dispatch --mode digest --force
```

`aitrack` normally dispatches automatically after it records terminal job states.
Manual `ainotify dispatch` is mainly for testing or retrying.

## Personal Message Backend Requirements

If CleverSlurm later adds true direct-message delivery, the Feishu configuration
will need different fields from the custom webhook backend:

```toml
[notification.feishu_app]
app_id_env = "AI_SLURM_FEISHU_APP_ID"
app_secret_env = "AI_SLURM_FEISHU_APP_SECRET"
receive_id_type = "open_id"
receive_id = "ou_..."
```

The implementation would need to:

1. Fetch `tenant_access_token` with app ID and app secret.
2. Resolve or store each target user's `open_id`, `user_id`, or target `chat_id`.
3. Call Feishu's IM send-message API with `Authorization: Bearer <token>`.
4. Handle app visibility, bot availability, message permissions, token caching,
   retries, and Feishu API errors.

That is intentionally separate from the current webhook path.

## Troubleshooting

If messages are not delivered:

- Check that `AI_SLURM_FEISHU_WEBHOOK` is exported in the same shell or service
  environment that runs `aitrack` or `ainotify`.
- If signing is enabled, check `AI_SLURM_FEISHU_SECRET`.
- If keyword validation is enabled, check that `AI-Slurm` is allowed.
- If IP allowlist is enabled, check the sender's actual public egress IP.
- Check `aijobs notifications` for pending or failed rows.
- Run `ainotify dispatch --mode all` manually and inspect the error output.

Official references:

- [Feishu custom bot usage guide](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)
- [Feishu send message API](https://open.feishu.cn/document/server-docs/im-v1/message/create)
- [Feishu tenant access token API](https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal)
