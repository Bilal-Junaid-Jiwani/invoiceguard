---
title: Configuration
eyebrow: Getting started
description: Every InvoiceGuard setting — config.yaml, environment variables, secret resolution order, and path overrides.
---
All settings live in `~/.invoiceguard/config.yaml` (written by `invoiceguard init`). Secrets can also come from environment variables — **env always wins over the YAML file, never the reverse.**

## The config file

```yaml
# Stripe — get test keys at https://dashboard.stripe.com/test/apikeys
stripe_secret_key: "sk_test_..."        # or env INVOICEGUARD_STRIPE_SECRET_KEY
stripe_webhook_secret: "whsec_..."       # or env INVOICEGUARD_STRIPE_WEBHOOK_SECRET

# SMTP — how dunning emails are sent
smtp:
  host: "smtp.gmail.com"
  port: 587
  username: "you@example.com"
  password: "APP_PASSWORD"              # or env INVOICEGUARD_SMTP_PASSWORD
  from_addr: "you@example.com"
  use_tls: true

# Web-facing base URL (used to point the Stripe webhook at this app)
app:
  base_url: "http://localhost:8000"
```

Never commit this file with real secrets. The repo's `.gitignore` covers `config.yaml`, `.env`, `*.key`, and the local DB.

## Environment variables

| Variable | Overrides |
|---|---|
| `INVOICEGUARD_STRIPE_SECRET_KEY` | `stripe_secret_key` |
| `INVOICEGUARD_STRIPE_WEBHOOK_SECRET` | `stripe_webhook_secret` (webhook endpoint) |
| `INVOICEGUARD_SMTP_PASSWORD` | `smtp.password` |
| `STRIPE_WEBHOOK_SECRET` | legacy alias for the webhook secret (lowest priority) |
| `INVOICEGUARD_CONFIG` | path of the config file itself (default `~/.invoiceguard/config.yaml`) |
| `INVOICEGUARD_DB` | path of the SQLite database (default `~/.invoiceguard/invoiceguard.db`) |

## Webhook secret resolution order

The dashboard's `POST /webhooks/stripe` endpoint resolves its signing secret like this:

1. `INVOICEGUARD_STRIPE_WEBHOOK_SECRET` environment variable
2. `stripe_webhook_secret` in the config file
3. Legacy `STRIPE_WEBHOOK_SECRET` environment variable (kept for backward compatibility)

Placeholder values (`whsec_...`, `sk_test_...`, `APP_PASSWORD`, anything ending in `...`) count as **unconfigured**. If no real secret is found, the endpoint returns HTTP 500 with a message telling you exactly what to set. See [Webhooks](webhooks.html).

## Templates

`~/.invoiceguard/templates/` holds `day1.md`, `day7.md`, `day15.md` — plain markdown with `{variables}`. `templates_dir()` resolves next to the config file, so a custom `INVOICEGUARD_CONFIG` keeps its templates alongside it. See [Dunning engine](dunning.html).

> [!WARNING]
> `invoiceguard check-due` refuses to run until `smtp.host` is set to something real — the template default (`smtp.example.com`) is rejected with an error telling you to edit the config first.
