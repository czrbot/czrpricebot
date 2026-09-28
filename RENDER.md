# Render cloud worker

The root render.yaml provisions one Singapore background worker and a 1 GB persistent disk. Auto-deploy waits for CI; preview instances are disabled. Review the cost in Render before applying changes.

The Docker entrypoint runs cloud.py. It hosts the scheduler and accepts only preview, configuration, and scheduler commands through an outbound authenticated connection to the private command center. No inbound port or Mac process is required. X publishing is hard-disabled by the command transport regardless of environment values.

Store CZR_AGENT_TOKEN and CZR_SITE_ACCESS_TOKEN in Render environment secrets. The matching BOT_AGENT_TOKEN belongs in the hosted dashboard secrets. Never put any secret in this repository. The site access credential allows entry through the private hosting layer, while the separate agent token authorizes worker endpoints. Rotate the agent token at cutover so only the cloud worker can update dashboard state.

Configuration is copied to /state/config.json on first boot and then persists across releases. The command center edits this copy. Scheduler start/pause persists in SQLite and defaults to running previews. Before the October 1, 2026 03:00 UTC launch boundary, no scheduled slot runs. Automatic posting remains disabled pending verified API semantics, approved formats, and tested alert routing. X credentials and a successful authorized connection-test post have been verified.

SQLite at /state/bot.sqlite3 stores commands, audit logs and deduplication state. Commands are marked pending before execution and are not replayed after an uncertain interruption. Connection errors use bounded backoff and emit generic errors without credentials. An unresponsive worker makes dashboard controls unavailable after 45 seconds. Use external service monitoring for prolonged worker outages.

Cutover: install secrets, deploy the cloud worker, stop the old Mac connector, rotate the matching dashboard agent token, verify the dashboard runtime is render, then run a preview and test scheduler controls. Do not run a separate bot daemon alongside cloud.py against the same state file.

## Guarded scheduled publishing

`CloudCenter` now supports the live scheduled path when `CZR_LIVE_POSTING=true`. Leave it false until provider field verification, exact-config SHA approval, X account ID and credentials, and alerts have been reviewed. Browser commands cannot enable live mode. Manual preview always uses ticker-only transport; scheduled live runs use the existing Bot guards, launch boundary, daily cap, freshness checks, durable pending reservations and X cooldowns. Slot reservations survive restarts and prevent repeat submissions. Ambiguous X outcomes stay pending and stop further live runs until reconciled manually. The scheduler does not replay missed slots or retry uncertain posts.

The dashboard currently uses preview-oriented labels; confirm runtime readiness and approval gates before switching to live mode. Configuration edits invalidate the approved SHA. State and deduplication live on the persistent /state disk.

## Email failure alerts

Use an authenticated SMTP provider. Store these values in Render Environment, never GitHub:

- SMTP_HOST: provider SMTP server
- SMTP_PORT: 587 (STARTTLS) or 465 (implicit TLS)
- SMTP_USERNAME and SMTP_PASSWORD: provider credentials
- ALERT_FROM_EMAIL: provider-authorized sender
- ALERT_TO_EMAIL: support@czrex.com

Webhook alerts remain supported through TEAM_ALERT_WEBHOOK_URL; when present, the webhook takes precedence. Remove it to use SMTP. Three consecutive component failures trigger an alert by default. Both successful and failed deliveries are throttled by the configurable cooldown. Exceptions are logged without credential values. An email provider must be connected and delivery tested before launch.

After saving and deploying email secrets, run in Render Shell:

```python
python -c "import bot; bot.alerts.send('CZR price bot alert delivery test. No market update or X post was sent.',bot.request)"
```

A successful SMTP acceptance is not proof of inbox delivery; confirm receipt at support@czrex.com. Network/service outages also require Render infrastructure notifications because an unavailable process cannot send its own alerts.

## Explicit dashboard-only exception

The owner approved dashboard/log-only failure reporting on September 28, 2026. Set `alert_mode` to `dashboard_only` in the persistent config only when explicitly approved. Repeated failures remain in durable events, including `alert_dashboard_only`; no external notification is sent in this mode. Team members must check the dashboard. Default mode remains `external`, requiring a configured delivery service. Config changes invalidate the approved hash.
