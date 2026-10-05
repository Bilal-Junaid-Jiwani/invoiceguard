---
title: Dashboard
eyebrow: User guide
description: The InvoiceGuard localhost web dashboard — invoice list, status filters, per-invoice timelines, and the Stripe webhook receiver.
---
```bash
invoiceguard dashboard            # http://127.0.0.1:8000/
invoiceguard dashboard --port 8080
```

A small FastAPI + Jinja2 web UI that shares the CLI's SQLite database. Binds to `127.0.0.1` by default (localhost only). The API docs (`/docs`, `/redoc`) are disabled.

## Pages

- **`GET /`** — invoice list with status filters (`draft`, `sent`, `partially-paid`, `paid`, `overdue`, `void`), totals (outstanding / overdue / collected), and per-status counts. Each row shows the invoice kind, amount, project, due date, current escalation stage — and, for partially-paid invoices, how much has been paid and what is left.
- **`GET /invoices/{id}`** — invoice detail: amount, paid and outstanding balances, status, Stripe payment link, due date, a **payments ledger** (every recorded payment with date, method, and note), and a chronological timeline of dunning events and payments.
- **`GET /healthz`** — `{"ok": true}`, for uptime checks.
- **`GET /sign/{token}`** — the client's one-time contract signing page: renders the contract, collects a typed name and a drawn signature (see [Contracts](contracts.html)).
- **`POST /sign/{token}`** — records the signature; returns a signed-receipt page. Unknown or already-used tokens return 404 / 400.
- **`POST /webhooks/stripe`** — the Stripe webhook receiver (see [Webhooks](webhooks.html)).

Unknown status filters and missing invoices return a 404 page.

## Screenshots

Captured from the real dashboard running locally against seeded demo data (every sample name is labeled `(demo)`):

![Invoice list](assets/img/dashboard-list.png)

*Invoice list — status filters, totals, and escalation stages.*

![Invoice detail](assets/img/dashboard-detail.png)

*Invoice detail — timeline of dunning events.*

![Invoice detail with partial payment](assets/img/dashboard-partial-payment.png)

*Invoice detail — a partially-paid invoice: Paid/Outstanding amounts, the payments ledger, and payment events in the timeline.*

## Notes

- The dashboard never inserts data on startup — it creates empty tables if the DB is fresh, nothing more.
- It reads and writes the same tables as the CLI; column names and types are frozen (additive migrations only). See [Database](database.html).
- Webhook verification needs the signing secret — resolution order is documented in [Webhooks](webhooks.html). Without a real secret, `POST /webhooks/stripe` returns HTTP 500.
- **Totals, defined:** *Outstanding* = sum of outstanding balances over `sent`/`overdue`/`partially-paid` invoices; *Overdue* = the same over `overdue` invoices only; *Collected* = sum of all rows in the payment ledger. (Since v0.2.0; for older databases the ledger backfill makes the numbers identical to the old definitions.)
