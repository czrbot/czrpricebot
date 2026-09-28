# Render Blueprint deployment

Create a Render Blueprint from this repository and review the paid worker and 1 GB disk cost before applying it. The root render.yaml defines one worker in Singapore. Auto-deploy waits for CI checks. Preview environments are disabled to prevent duplicate scheduled bot instances.

The initial deployment is dry-run only. The Docker command explicitly uses --dry-run; setting an environment variable alone cannot enable live posting. API provider verification, final format approval, runtime secrets and alert configuration remain required before a separately reviewed live deployment.

The launch guard is October 1, 2026 at 03:00 UTC (11 a.m. Singapore). Six price slots and one daily summary are configured, subject to validation, freshness and unchanged-data skips. SQLite audit and deduplication state persist at /state/bot.sqlite3.

This repository currently deploys the standalone bot worker. The existing hosted command-center relay still needs migration and connection to the cloud runtime; deploying this Blueprint alone does not migrate those controls. Keep live publishing disabled until that migration and the single-worker cutover are verified.

Never commit environment files, credentials, state databases or logs. Enter X secrets directly into Render environment settings only when activating the reviewed live deployment. No X secrets are needed for this dry-run worker.
