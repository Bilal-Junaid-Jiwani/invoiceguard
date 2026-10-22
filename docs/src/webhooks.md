---
title: Webhooks
eyebrow: User guide
description: Stripe webhook setup for InvoiceGuard — endpoint, signing-secret resolution order, verified events, offline testing.
---
`POST /webhooks/stripe` (served by the [dashboard](dashboard.html)) lets Stripe tell InvoiceGuard when a client pays. On a verified `checkout.session.completed` event whose payment has actually settled, the matching invoice is marked `paid` automatically.

## Setup

1. Stripe dashboard → **Developers → Webhooks → Add endpoint**.
2. URL: `https://YOUR-HOST/webhooks/stripe`.
3. Select the events `checkout.session.completed` and `checkout.session.async_payment_succeeded` (the second is how delayed payment methods like ACH report that the money actually arrived).
4. Copy the **Signing secret** (`whsec_...`) and configure it (below).

## Signing-secret resolution order

The endpoint resolves the secret like this — first hit wins:

1. `INVOICEGUARD_STRIPE_WEBHOOK_SECRET` environment variable
2. `stripe_webhook_secret` in `~/.invoiceguard/config.yaml`
3. Legacy `STRIPE_WEBHOOK_SECRET` environment variable (backward compatibility)

Placeholder values (`whsec_...`, anything ending in `...`) count as **unconfigured**. With no real secret, the endpoint returns **HTTP 500** with a message telling you exactly what to set. Bad signatures return **HTTP 400**.

> [!NOTE]
> This resolution order is a v0.1.0 fix. Previously the endpoint only honored the undocumented bare `STRIPE_WEBHOOK_SECRET` env var, so anyone following the README got 500s. See the [changelog](changelog.html).

## Events handled

| Stripe event | Result |
|---|---|
| `checkout.session.completed` | invoice marked `paid` (matched by the `invoiceguard_invoice_id` metadata on the payment link; `invoice_id` is a tolerated legacy alias; falls back to matching `stripe_session_id`) — **only when the session's `payment_status` is `paid`** (or absent, as in older payloads) |
| `checkout.session.async_payment_succeeded` | same as above — this is the event that marks the invoice paid for delayed payment methods |
| `payment_link.expired` | ignored — nothing is auto-voided in v1; void from the CLI or dashboard |
| anything else | ignored |

**Delayed payments (0.8.4):** for ACH debits and other delayed methods, `checkout.session.completed` fires with `payment_status: unpaid` while the money is still in transit. That event now records **nothing** (`ignored:payment_unpaid`) — before 0.8.4 the invoice was marked paid on money that had not arrived. When the payment clears, Stripe sends `checkout.session.async_payment_succeeded` and the invoice is marked paid then.

Already-paid invoices are left alone (`ignored:already_paid`); **void** invoices are likewise ignored — void is terminal, so a webhook never resurrects one; events without invoice metadata are ignored.

**Idempotent on event id (v0.7.1):** every processed event's Stripe id is recorded in a `webhook_events` table, so a retried delivery (Stripe resends events until it gets a 2xx) returns `ignored:duplicate_event` instead of re-recording. Before this, replaying the same partial-payment event recorded a second payment row — enough retries could mark an invoice fully-paid on half the money. Distinct events for the same invoice are still processed normally.

## Offline smoke test

Signature verification uses real `stripe.Webhook.construct_event` HMAC checks and works with no network:

```bash
INVOICEGUARD_DB=/tmp/ig-demo.db python seed_demo.py --fresh
INVOICEGUARD_DB=/tmp/ig-demo.db INVOICEGUARD_STRIPE_WEBHOOK_SECRET=whsec_test_x \
  invoiceguard dashboard
# POST a locally-signed test payload to http://127.0.0.1:8000/webhooks/stripe
```

The test suite covers this path end to end, including the metadata-key regression (`invoiceguard_invoice_id` vs `invoice_id`) and the secret-resolution regression.
