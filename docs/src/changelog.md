---
title: Changelog
eyebrow: Reference
description: InvoiceGuard release history — newest first, no fluff.
---
InvoiceGuard follows semver. Newest first.

## [0.2.0] - 2026-10-04

Partial payments: invoices no longer have to be all-or-nothing.

### Added

- Payment ledger: new `payments` table (additive migration; pre-0.2.0 paid invoices get a labeled backfill row on first open, so old databases keep working)
- `invoice record-payment <id> --amount <dollars> [--note ...] [--method ...]` — record a partial payment; a payment that clears the balance marks the invoice `paid`, otherwise the invoice becomes `partially-paid`
- Dashboard: per-invoice Paid/Outstanding amounts, a payments ledger on the invoice detail page, payment events in the timeline, a new `partially-paid` status filter chip, and totals computed from outstanding balances
- Dunning emails now show paid-so-far and the remaining balance (`{paid}` / `{outstanding}` template variables; the three built-in templates use them)

### Changed

- `invoice mark-paid` now records the full outstanding balance in the ledger (was: status flip with no payment record); it errors instead of silently re-marking an already-paid or void invoice
- Stripe webhooks record the session amount in the ledger (method `stripe`); a session for less than the balance leaves the invoice `partially-paid` instead of `paid`
- Dashboard totals: *Collected* is now the sum of recorded payments, *Outstanding*/*Overdue* are sums of outstanding balances (identical numbers for pre-0.2.0 databases thanks to the backfill)

## [0.1.0] - 2026-10-02

First release on PyPI (`pip install invoiceguard`) and GitHub.

### Added

- CLI: `init`, `client add/list`, `project create/list/ack`, `invoice create/list/mark-paid/void`, `check-due`, `dashboard`
- Stripe deposit payment links per project (test-mode friendly), with `invoiceguard_invoice_id` metadata so webhook payments map back to invoices
- Late-fee contract generator: deposit + late-fee clause markdown, client acknowledgment recorded when the deposit link is accepted
- Automated 3-stage dunning: day-1 polite nudge → day-7 firm reminder → day-15 formal notice quoting the late-fee clause; idempotent, cron-friendly (`check-due`), editable markdown templates
- Localhost web dashboard: invoice list with status filters and escalation stages, per-invoice detail with chronological timeline, totals/outstanding/overdue/collected, Stripe webhook receiver that marks invoices paid
- SQLite persistence (shared schema between CLI and dashboard), Docker Compose setup

### Fixed

- **Dashboard Stripe webhook secret resolution.** The README told users to set `stripe_webhook_secret` in the config (or `INVOICEGUARD_STRIPE_WEBHOOK_SECRET`), but the dashboard only honored the bare `STRIPE_WEBHOOK_SECRET` env var — webhooks returned 500 for anyone following the docs. The endpoint now resolves, in order: `INVOICEGUARD_STRIPE_WEBHOOK_SECRET` env → `stripe_webhook_secret` in the config file → legacy `STRIPE_WEBHOOK_SECRET` env (still supported). Placeholder values (`whsec_...`) count as unconfigured. Regression tests cover all four cases.

### Honest scope (v1)

- The "signature" is a client-acknowledgment checkbox (deposit-link acceptance), not a legal e-signature — real e-signature is on the roadmap.
- Email deliverability is the user's own SMTP; late-fee enforceability varies by jurisdiction.
