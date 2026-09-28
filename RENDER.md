# Render cloud worker

The root render.yaml provisions one Singapore background worker and a 1 GB persistent disk. Auto-deploy waits for CI; preview instances are disabled. Review the cost in Render before applying changes.

The Docker entrypoint runs cloud.py. It hosts the scheduler and accepts only preview, configuration, and scheduler commands through an outbound authenticated connection to the private command center. No inbound port or Mac process is required. X publishing is hard-disabled by the command transport regardless of environment values.

Store CZR_AGENT_TOKEN and CZR_SITE_ACCESS_TOKEN in Render environment secrets. The matching BOT_AGENT_TOKEN belongs in the hosted dashboard secrets. Never put any secret in this repository. The site access credential allows entry through the private hosting layer, while the separate agent token authorizes worker endpoints. Rotate the agent token at cutover so only the cloud worker can update dashboard state.

Configuration is copied to /state/config.json on first boot and then persists across releases. The command center edits this copy. Scheduler start/pause persists in SQLite and defaults to running previews. Before the October 1, 2026 03:00 UTC launch boundary, no scheduled slot runs. All posting remains disabled pending verified API semantics, approved formats, X credentials, alert routing, and a reviewed publishing implementation.

SQLite at /state/bot.sqlite3 stores commands, audit logs and deduplication state. Commands are marked pending before execution and are not replayed after an uncertain interruption. Connection errors use bounded backoff and emit generic errors without credentials. An unresponsive worker makes dashboard controls unavailable after 45 seconds. Use external service monitoring for prolonged worker outages.

Cutover: install secrets, deploy the cloud worker, stop the old Mac connector, rotate the matching dashboard agent token, verify the dashboard runtime is render, then run a preview and test scheduler controls. Do not run a separate bot daemon alongside cloud.py against the same state file.
