---
title: Dashboard
eyebrow: User guide
description: The InvoiceGuard localhost web dashboard — invoice list, status filters, per-freelancer payout timing, per-invoice timelines, and the Stripe webhook receiver.
---
```bash
invoiceguard dashboard            # http://127.0.0.1:8000/
invoiceguard dashboard --port 8080
```

A small FastAPI + Jinja2 web UI that shares the CLI's SQLite database. Binds to `127.0.0.1` by default (localhost only). The API docs (`/docs`, `/redoc`) are disabled.

## Pages

- **`GET /`** — invoice list with status filters (`draft`, `sent`, `partially-paid`, `paid`, `overdue`, `void`), totals (outstanding / overdue / collected), and per-status counts, plus a Payouts section with per-freelancer payout timing (due / overdue / upcoming / settled / no schedule, with next or missed payout date, as of today — the same rollup `payout schedule` and `freelancer list` use; hidden when the roster is empty, and freelancers with no billable invoices get no row). Append `?as_of=YYYY-MM-DD` to project the timing to that day instead (the header then reads `payout timing as of <date>` with a "back to today" link, and the status-filter and Details links keep the projection); an invalid date is a clean 400. Only the payout timing is projected — the invoice list, totals, and escalation stages stay as-of-today. Each row shows the invoice kind, amount, project, due date, current escalation stage — and, for partially-paid invoices, how much has been paid and what is left.
- **`GET /invoices/{id}`** — invoice detail: amount, paid and outstanding balances, status, Stripe payment link, due date, a **payments ledger** (every recorded payment with date, method, and note), and a chronological timeline of dunning events and payments. When the invoice is assigned to a freelancer, the Freelancer row also shows their payout timing (due / overdue / upcoming / settled / no schedule, with next or missed payout date, as of today — the same rollup as the index Payouts section; no phrase when the freelancer has no billable invoices). `?as_of=YYYY-MM-DD` projects that timing to the day (marked `(as of <date>)`, back link keeps the projection); the amounts and timeline stay as-of-today, and an invalid date is a clean 400.
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
