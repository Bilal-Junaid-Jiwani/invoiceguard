---
title: Quickstart
eyebrow: Getting started
description: The 5-minute InvoiceGuard flow — init, Stripe test key, SMTP, first client, first deposit link, automated collections.
---
Five minutes, five steps. Nothing here touches real money until you decide it should.

## 0. Install and init

```bash
pip install invoiceguard
invoiceguard init     # creates ~/.invoiceguard (config template, DB, email templates)
```

## 1. Add your Stripe test key (~2 min)

1. Go to <https://dashboard.stripe.com/test/apikeys> (toggle **Test mode** on).
2. **Developers → API keys → Create secret key**, copy the `sk_test_...` value.
3. Paste it into `~/.invoiceguard/config.yaml` as `stripe_secret_key`, **or** export it:

```bash
export INVOICEGUARD_STRIPE_SECRET_KEY="sk_test_..."
```

## 2. Add your SMTP details (~2 min)

Dunning emails are sent through *your* SMTP. In `~/.invoiceguard/config.yaml`:

```yaml
smtp:
  host: "smtp.gmail.com"
  port: 587
  username: "you@example.com"
  password: "YOUR_GMAIL_APP_PASSWORD"   # or export INVOICEGUARD_SMTP_PASSWORD
  from_addr: "you@example.com"
  use_tls: true
```

(Gmail: create an **App Password** at Google Account → Security → 2-Step Verification → App passwords. Any SMTP provider works.)

## 3. Create a client, project, and deposit link (~1 min)

```bash
invoiceguard client add --name "Acme Corp" --email "billing@acme.com"

invoiceguard project create --client "Acme Corp" --title "Website redesign" \
  --amount 2000 --deposit-pct 50 --late-fee-pct 1.5 --ack
```

This generates the contract markdown (with the late-fee clause) and — with `--ack` — records the client's acknowledgment. Then:

```bash
invoiceguard invoice create --project 1 --kind deposit
# => invoice #1 (deposit): $1,000.00
#    pay link: https://buy.stripe.com/...
#    due: 2026-10-07   status: sent
```

Send the pay link to the client. That's the whole pre-work flow.

## 4. Let collections run themselves

Add the dunning scan to your crontab (every morning at 9:00):

```cron
0 9 * * * /path/to/venv/bin/invoiceguard check-due >> ~/.invoiceguard/check-due.log 2>&1
```

`check-due` scans invoices past their due date and sends the next escalation stage (day1 → day7 → day15) using the editable markdown templates in `~/.invoiceguard/templates/`. It's idempotent — a stage is never sent twice for the same invoice.

## 5. Stripe webhook (so payments mark invoices paid)

1. In the Stripe dashboard: **Developers → Webhooks → Add endpoint**.
2. URL: `https://YOUR-HOST/webhooks/stripe` (served by the dashboard app; see [Dashboard](dashboard.html)).
3. Select event: `checkout.session.completed`.
4. Copy the **Signing secret** (`whsec_...`) into `stripe_webhook_secret` in the config, or export `INVOICEGUARD_STRIPE_WEBHOOK_SECRET`.

The dashboard resolves the secret in this order: `INVOICEGUARD_STRIPE_WEBHOOK_SECRET` env → `stripe_webhook_secret` in the config file → legacy `STRIPE_WEBHOOK_SECRET` env. Full detail in [Webhooks](webhooks.html).

## Next

- [Configuration](configuration.html) — every setting, env var, and override
- [CLI reference](cli.html) — the full command list
- [Dashboard](dashboard.html) — the web UI
