---
title: Docker
eyebrow: User guide
description: Run InvoiceGuard with Docker Compose — CLI container, persistent data volume, dashboard port, one-off dunning scans.
---
The repo ships a `Dockerfile` and `docker-compose.yml` for running InvoiceGuard in containers.

## Up

```bash
docker compose up -d
```

- Runs the CLI container; data persists in the `invoiceguard-data` volume.
- Port **8000** is exposed for the dashboard.

## One-off dunning scan

```bash
docker compose run --rm app check-due
```

Any CLI command works the same way: `docker compose run --rm app client list`, and so on.

## Secrets

Put secrets in a `.env` file next to `docker-compose.yml` (**never committed**):

```env
INVOICEGUARD_STRIPE_SECRET_KEY=sk_test_...
INVOICEGUARD_STRIPE_WEBHOOK_SECRET=whsec_...
INVOICEGUARD_SMTP_PASSWORD=...
```

The same `INVOICEGUARD_*` variables documented in [Configuration](configuration.html) apply inside the container.

## Notes

- The container uses the same SQLite schema as a local install ([Database](database.html)); the volume keeps it across restarts.
- Cron-style scheduling (`check-due`) is your host's job — run the one-off command from your system crontab, or schedule the container externally.
