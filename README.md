See [RENDER.md](RENDER.md) for the cloud command-center worker. The standalone CLI below remains available for diagnostics.

# CZR price bot

A Python 3.11+ service for `@czrpricebot`. No third-party Python packages. **Deployed on Render; automatic live posting remains disabled.** The workspace had no existing code or deployment setup.

The configured public endpoint is:

`https://openapi.czrex.com/sapi/v2/ticker?symbol=czrtoken1858usdt1858`

This URL was supplied by the owner and returned JSON in a read-only check on September 28, 2026. That check establishes reachability, not API provider confirmation of the field calculations. The supplied 0.025 USDT reference price is not used anywhere in the bot. No invented or fallback price is published. Returned prelaunch values are not evidence that public trading has opened.

## Quick start and exact dry runs

Run commands from this directory with Python 3.11 or newer:

```sh
python3 -m unittest discover -s tests -v
python3 bot.py --dry-run --preview-unverified --kind price --state state/preview.sqlite3
python3 bot.py --dry-run --preview-unverified --kind daily --state state/preview.sqlite3
```

`--preview-unverified` is a read-only diagnostic. It fetches the configured endpoint using provisional `last` and `time` mappings. Stderr prominently labels the output as unverified; stdout contains only the exact proposed post. It refuses 24-hour metrics and cannot be combined with live posting or the daemon. It still checks that the provisional timestamp is recent; this is not proof that `time` measures data freshness. A repeated preview in the same slot is skipped; use a separate scratch state path when intentionally reviewing another preview.

Normal `--dry-run` requires completed API provider verification and produces exactly the same text as live mode. No X authorization, X calls, or posting occurs in dry-run mode. Preview, fixture, normal dry-run, and live deduplication histories are separate.

Offline deterministic test, using clearly synthetic fixture values:

```sh
python3 bot.py --dry-run --fixture examples/synthetic-ticker.json --at 2026-10-01T00:00:00Z --state state/fixture.sqlite3
```

Fixture values are test inputs only. The CLI forbids publishing fixtures or overriding the clock in live mode. Exit code 0 indicates an emitted post/preview, 2 indicates a skip or refusal. Detailed JSON audit events explain the result on stderr and in SQLite.

Run the tests above to verify the bot safeguards.

## Configuration and schedule

Edit `config.json`. No credentials belong in this file.

- Price updates: every four hours, aligned to **03:00, 07:00, 11:00, 15:00, 19:00, 23:00 UTC**.
- Daily summary: **15:10 UTC**, independently scheduled and deduplicated.
- The daily summary initially contains a price snapshot only. Verified 24-hour metrics can be enabled later; it does not claim to summarize the prior UTC calendar day.
- Freshness: 300 seconds, with 30 seconds of permitted future clock skew. Synchronize the deployment host with NTP.
- Schedule grace: 300 seconds. The worker polls every 30 seconds. Missed slots outside the grace period are skipped, never backfilled in a burst.
- Each daemon slot is attempted once, including failures. Ticker GET retries within an attempt are bounded to three; no repeated POST within a slot. After a rejected post, recovery resumes at a later scheduled slot.
- `interval_hours`, `daily_utc`, freshness, grace, alert threshold/cooldown, mappings, and both templates are configurable. Interval hours must divide 24.
- Template placeholders: `{price}`, `{timestamp}`, `{link}`, optional `{stats}`. Templates must retain source, pair, UTC timestamp, and the exact provided trade link. Keep edits factual and neutral; the configuration approval hash changes whenever the configuration changes.

To enable metrics later, record each relevant API provider confirmation, set the verified volume unit and mapping, and set `include_24h` to true. The bot independently computes `(last - open) / open * 100` using Decimal; it does not trust `rose`. It rejects nonfinite values, negative volumes, nonpositive prices, inconsistent ranges, missing required fields, and stale/future timestamps. Unused bid/ask fields are never presented. The configured single-symbol response may omit `symbol`; the request itself is bound to the exact identifier. A returned mismatched symbol is rejected.

## API provider verification required

Use [API-VERIFICATION.md](API-VERIFICATION.md). No API provider representative has confirmed the outstanding semantics during this task. Record a dated, auditable support response/document reference in `provider_verification.reference` and `confirmed_at`, then put the actual confirmed meaning in each applicable field. Do not fill these with guesses or test strings.

The production price path requires confirmations for base URL, symbol/query scoping, last price, and timestamp. The metrics path additionally requires window, high, low, open, and volume calculations. The provided generic docs label `time` as **Open Time**, so merely observing a recent numeric timestamp is insufficient. If it is not an authoritative last-trade/market-update timestamp, the adapter needs an additional confirmed timestamp endpoint before live posting can be approved.

## X authorization and final approval

Use X's official API only: `GET https://api.x.com/2/users/me` and `POST https://api.x.com/2/tweets`. Before every post the bot checks both `czrpricebot` and the authorized account's immutable user ID.

For unattended deployment, use OAuth 1.0a user credentials authorized by **@czrpricebot**, with read/write access. Inject these from the deployment secret manager:

- `X_API_KEY`, `X_API_KEY_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`
- `X_EXPECTED_USER_ID`: the verified numeric ID of @czrpricebot
- `TEAM_ALERT_WEBHOOK_URL`: HTTPS team webhook accepting `{"text":"..."}` and returning a 2xx acknowledgement

Alternatively `X_USER_ACCESS_TOKEN` accepts an OAuth 2.0 **user** token with `tweet.read`, `tweet.write`, and `users.read`. An app-only bearer token cannot pass the user identity check. OAuth 2.0 token refresh is not implemented here: provision rotation/restart through a secret manager or use OAuth 1.0a for the long-running worker. Do not use a short-lived OAuth 2.0 token as an unattended credential without a rotation setup.

The owner must approve the final rendered price/daily formats and configuration before activation. The bot additionally requires **all** of:

1. Completed API provider evidence and verified mappings.
2. Authorized X credentials, expected user ID, and a configured webhook or TLS SMTP sender.
3. `CZR_APPROVED_CONFIG_SHA256` set to the output of `python3 bot.py --config-hash`, after approval.
4. `CZR_LIVE_POSTING=true` and the explicit `--live` CLI flag.

X credentials and account ID are installed and verified. Provider evidence, approval and tested alerts are still required before automatic activation. Do not paste credentials into chat, commit them, or put them in command-line arguments. The application does not automatically load `.env` files.

## Deployment

A disabled Docker Compose example is included:

```sh
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml up -d
```

The Compose example starts the cloud command-center worker with publishing locked; it requires completed API provider verification to render production dry runs. It never enables live posting. For a local worker use `python3 bot.py --daemon --dry-run`.

After verification, credentials, and explicit owner approval, the deployment operator can override the command with `python bot.py --daemon --live --state /state/bot.sqlite3`, inject the secrets/approval variables, and remove the sample's forced false value. Do not use multiple replicas or multiple state volumes for the same X account. Keep the durable SQLite volume across restarts, deployments, and rollbacks. Losing it loses duplicate protection.

The container runs as UID 10001, with a read-only root filesystem, dropped capabilities and bounded container logs. Ensure the state volume is writable by that UID. The Docker worker is deployed on Render; see RENDER.md for cloud operations. Stop with `docker compose -f deploy/compose.yaml stop`; restarting retains state. External uptime monitoring should detect a stopped worker, disk-full conditions, and missing expected scheduler activity.

## Failures, duplicate prevention, and audit

SQLite stores every scheduled attempt, retrieval attempt/result, failure, skip, alert delivery, exact proposed text, and successful X post ID. It uses full synchronous commits and a process lock. Logs do not include credentials, HTTP response bodies, or webhook URLs. Back up state securely and rotate/archive audit records under an operational retention policy; the SQLite audit table is not auto-pruned.

Market fingerprints exclude timestamps and compare the displayed price/metrics with the last successful update of the same type. An unchanged market skips the next update even if the API timestamp advances. Daily summaries are separate from four-hour prices so their schedules do not suppress each other.

Ticker transient GET failures use bounded exponential backoff with jitter. 410/418/429 preserve a cooldown, honoring server reset information and a conservative minimum. X rate limits also persist across restarts. Credentials/permission errors are logged and not blindly retried. Repeated ticker or posting failures alert after three failed scheduled attempts; successful component operation resets its streak. Alert delivery is rate limited to hourly per component; failed delivery is logged and throttled by the same cooldown. The alert destination must be configured for live mode; no alert has actually been sent during development.

A durable `pending` record is committed before sending to X. A timeout, 5xx, malformed success body, or crash can mean X accepted the post without returning its ID. The worker **does not retry** such submissions: all subsequent publishing is blocked pending reconciliation. Exactly-once remote delivery cannot be guaranteed across an ambiguous network failure; this conservative stop avoids automatic duplication.

To reconcile, stop the worker and inspect `jobs` plus the account's posts using X's official API. If the post exists, record its ID, mark the job `posted`, and atomically update `kv['previous:live:<kind>']` to the job's fingerprint (JSON string). If absence is established, mark it `rejected`; do not delete the row or retry that slot. Insert an audit event with the investigation evidence, back up the database, and restart only after all pending jobs are resolved. Never clear a pending entry merely because a request timed out.

## Sources

- [CZR live trading page](https://www.czrex.com/en_US/trade/CZR_USDT?type=spot)
- [API provider spot API documentation](https://exchangedocsv2.gitbook.io/open-api-doc-v2/spot)
- [API provider API basics](https://exchangedocsv2.gitbook.io/open-api-doc-v2/openapi-basic-information)
- [X create-post API](https://docs.x.com/x-api/posts/create-post)
- [X official API examples](https://github.com/xdevplatform/samples/blob/main/python/posts/create_post.py)

## October 1 launch plan

Launch is confirmed by the owner for **October 1, 2026 at 11:00 Singapore time (03:00 UTC)**. `start_at_utc` enforces this lower bound. Price slots are anchored at 03:00 UTC, repeating every four hours; daily summary is at 15:10 UTC (23:10 Singapore). This yields seven opportunities per UTC day, with a hard maximum of seven successful scheduled posts. Missing, invalid, stale, or unchanged data may result in fewer actual posts. All content concerns CZR/USDT only.

The schedule is configured but **not armed for live posting**. API provider price/timestamp verification, final format approval, and the tested team alert destination are still required. Manual read-only previews remain available before launch. Start a verified live worker only after those requirements are complete; it will skip attempts before the configured launch instant.
