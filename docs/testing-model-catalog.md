# Model catalog and saved-selection verification

## Safe procedure

Use a separate `CSLURM_ROOT`, a separate web port, and a fresh empty database.
Disable notifications and automatic submission summaries in this test root.
Never copy production jobs or the notification queue into the test database.
Credentials belong in private configuration files, not test sources or reports.

1. Open web settings and fetch the catalog using the selected provider profile.
2. Search and select a model from the returned list, then save.
3. Reload settings and verify that the selected model persisted.
4. For a named profile, use **Set as current** in the isolated root. Saving a
   named profile alone deliberately does not replace the runtime `[ai]` section.
5. Test the saved connection, then start a fresh Python process with the same
   `CSLURM_ROOT`. Construct `ModelClient()` without overriding its provider or
   model. Record only the outgoing URL and JSON `model`, not headers or keys.
6. Verify the outgoing model, successful response, and `last_model` all agree.
   Disable fallback models and retries so another model cannot mask a failure.
7. Check that production configuration hashes are unchanged. Stop only the
   test web processes and their dedicated SSH tunnels when finished.

Catalog availability, chat entitlement, and host connectivity are separate
checks. A model appearing in a catalog does not prove a paid subscription is
active. An HTTP 403 is not the same failure as a TCP connection timeout.

## Live results: 2026-10-23

These are observations from this run, not a permanent compatibility guarantee.

| Provider / host | Catalog | Web selection and persistence | Actual chat |
| --- | --- | --- | --- |
| SiliconFlow / management host | 98 models | Selected `Qwen/Qwen2.5-7B-Instruct`, saved and activated in isolated configuration | Fresh process sent that exact model to `/v1/chat/completions`; returned `OK` in 0.31 seconds; no fallback |
| Kimi Code / local computer | 4 models | Selected `kimi-for-coding`, saved, reloaded, and confirmed persisted | Sent that exact model to `/coding/v1/messages`; HTTP 403 `permission_error`: current subscription has no Kimi Code access |
| Kimi Code / management host | Connection timeout before HTTP response | Not treated as a successful catalog fetch | TCP connection to `api.kimi.com:443` timed out in an independent 8-second probe; no proxy environment variables were set |

The Kimi catalog returned `k3`, `k3-256k`, `kimi-for-coding`, and
`kimi-for-coding-highspeed`. Catalog access succeeded locally with the same key
that was denied chat access. Do not work around that denial by claiming another
model succeeded; subscription access must be resolved with the provider.

A later SiliconFlow web connection-test repetition hit the browser request
timeout. The initial web test and independent fresh-process request succeeded;
these results establish that the saved selection is used, not a guarantee of
consistent provider/network latency.

Production model/Feishu settings were not changed. No Slurm jobs were submitted
or cancelled, and no test or historical Feishu notifications were sent.

## Automated regression coverage

`tests/test_model_discovery.py` covers protocol headers, endpoint fallback,
pagination, error handling, response limits, and redirect credential protection.
`tests/test_web_settings.py::test_saved_selection_is_used_by_fresh_process`
verifies both OpenAI and Anthropic formats: save a profile, activate it, start a
new process, and assert the exact outgoing model and `last_model`. Its transport
is mocked, so normal CI neither consumes API credits nor needs credentials.
