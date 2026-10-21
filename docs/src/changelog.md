---
title: Changelog
eyebrow: Reference
description: InvoiceGuard release history — newest first, no fluff.
---
InvoiceGuard follows semver. Newest first.

## [0.8.3] - 2026-10-21

E-signature contract snapshot: `sign-request` now freezes the project's contract text into the signature request at link-creation time. The signing page renders that snapshot and the SHA-256 tamper-evidence hash covers it — previously the page and the hash used the live project text, so a contract edited after the link was created (but before signing) produced a hash over text the client never saw. Requests created before v0.8.3 have no snapshot and fall back to the current project text; existing databases get the new `signatures.contract_md` column additively on connect.

## [0.8.0] - 2026-10-16

Freelancer roster + invoice attribution — the second agency-mode slice. `invoiceguard freelancer add/list/set-email/set-phone` manages the agency roster (name, email, phone); `invoiceguard invoice assign <id> --freelancer <id>` (or `--none` to clear) attributes an invoice to a freelancer. Attribution is pure bookkeeping — it never changes dunning, payments, or invoice status. `invoice list --freelancer ID` filters by freelancer, and `invoice report` gains a `--freelancer ID` filter plus a "by freelancer" breakdown; the invoice CSV gets a trailing `freelancer` column, and the dashboard shows the attributed freelancer on the invoice list and detail pages. Existing databases get the `freelancers` table and the `invoices.freelancer_id` column additively on connect (NULL = unattributed). Honest scope: no per-freelancer logins, permissions, or workspaces — the remaining half of agency mode (multi-freelancer workspaces) is still unshipped.

## [0.7.1] - 2026-10-15

Stripe webhook idempotency: processed Stripe event ids are now recorded in a `webhook_events` table, so a retried delivery of an already-handled event is ignored (`ignored:duplicate_event`) instead of re-recorded. A retried partial-payment webhook previously recorded a second payment row — replaying the same half-payment event could mark the invoice fully-paid on half the money. Distinct event ids for the same invoice are still processed normally. Existing databases get the table additively on connect.

## [0.7.0] - 2026-10-14

Per-client dunning policies — the first agency-mode slice. Each client can now have its own dunning stage thresholds via `invoiceguard client set-dunning-policy <id> [--day1 N] [--day7 N] [--day15 N] [--reset]`: a positive number sets a custom days-overdue threshold, `0` disables the stage for that client, and thresholds must keep escalation order (day1 → day7 → day15). `check-due` resolves the policy per invoice, so one client can escalate faster while another is never auto-dunned; an invoice whose client has every stage disabled is reported as `skipped dunning (invoice #N)` instead of silently vanishing. `client list` shows a `[dunning ...]` marker for clients with overrides. Existing databases are migrated additively on connect (new columns, NULL = global default). Only *when* a stage fires changes — templates, subjects, and idempotency are untouched; multi-freelancer workspaces remain unshipped.

## [0.6.5] - 2026-10-13

Dunning send-failure isolation: one invoice's email send failing no longer aborts the whole `check-due` run. Verified: a transient SMTP failure on the first overdue invoice propagated out and killed the run, so every other overdue invoice never got its stage. Now a failed send is reported as `send_error`, nothing is recorded for that invoice (the stage retries on the next run), and the rest of the batch still goes out.

## [0.6.4] - 2026-10-12

Webhook void-safety: the dashboard's Stripe webhook handler (`POST /webhooks/stripe`) no longer records a payment or changes status when the matching invoice was voided. Verified: a void invoice came back as `paid` with a `stripe` ledger row — now void is terminal on the webhook path, matching the core `record_payment()` rule that refuses payments on void invoices. Honest scope: only the webhook path changed; already-paid invoices stay idempotent.

## [0.6.3] - 2026-10-11

Dunning robustness: `check-due` used to crash the whole collections run (`ValueError: recipient address is empty`) when any overdue invoice's client had no email on file — easy to hit, since `client add` makes `--email` optional. Now such invoices are skipped with a reported reason, nothing is recorded for them, and every other overdue invoice still gets its email. New: `invoiceguard client set-email <id> [--email ...]` to add or clear a client's email (dunning) after creation.

## [0.6.2] - 2026-10-10

Contract deposit fix: the deposit figure printed in the generated contract now always matches the quoted (and charged) deposit. The contract previously used float + banker's rounding, so a $10.05 project at 50% said $5.02 in the contract while the CLI quoted and charged $5.03. It now uses Decimal half-up, the same money rule as CLI amounts and late fees. Honest scope: only newly generated contract text changes, only on half-cent boundaries; stored contracts and invoices are untouched.

## [0.6.1] - 2026-10-09

CLI money-parsing fix: `--amount` values are parsed with Decimal half-up instead of float + `round()`, so `--amount 1.005` records 101 cents, not 100; non-positive or non-numeric amounts are rejected before anything is written. Honest scope: only CLI-entered amounts changed; stored cents and Stripe session amounts were already exact.

## [0.6.0] - 2026-10-08

Receivables report: `invoiceguard invoice report [--as-of YYYY-MM-DD]` — read-only aging over open invoices (Current / 1-30 / 31-60 / 61-90 / 90+ day buckets, per-client totals, accrued late fees via the same §3 calculator as `invoice late-fees`), with `--csv` / `--payments-csv` exports for accounting. "Outstanding" matches the dashboard totals; drafts, paid, and void invoices are excluded. See [CLI reference](cli.html). Honest scope: read-only; fees are computed on the current outstanding balance as of the report date.

## [0.5.0] - 2026-10-07

SMS / WhatsApp escalation: the day-15 formal notice can now also reach the client as a short message via Twilio (SMS or WhatsApp), recorded in a new `message_events` ledger. New: `invoice notify <id> [--channel sms|whatsapp] [--dry-run]`, `client add --phone`, `client set-phone`. See [Dunning engine](dunning.html) and [Configuration](configuration.html). Honest scope: needs your own Twilio account; no live Twilio call was verified in the build environment.

## [0.4.0] - 2026-10-06

Late-fee accrual calculator: the contract has always promised "{late_fee_pct}% per month, compounding monthly" after the grace period — now InvoiceGuard computes the actual number.

### Added
- `invoiceguard invoice late-fees <id> [--as-of YYYY-MM-DD]` — accrual breakdown: due date, grace window, rate, months billed, accrued fees, total due. Fees start accruing the day after the grace period ends; each started calendar month charges one full monthly fee, compounding on the running balance, rounded half-up to the cent.
- Dashboard invoice detail page: "Accrued late fees" and "Total with fees" rows under Contract & terms.
- New dunning template variables: `{late_fee_due}` and `{total_with_late_fees}`. The shipped `day15.md` formal notice now quotes the actual accrued fees and total due.

### Honest scope (late fees)
- A partial month counts as a full month; the calculator uses the current outstanding balance and does not retroactively amortize mid-accrual payments. Late-fee enforceability varies by jurisdiction.

## [0.3.0] - 2026-10-05

Real e-signature: clients can now sign the contract electronically instead of (or in addition to) acknowledging via the paid deposit link.

### Added

- E-signature capture: `invoiceguard project sign-request <id>` creates a one-time, single-use signing link (`/sign/<token>`, served by the dashboard). The client reads the rendered contract, types their full name, and draws a signature on a canvas (mouse or touch).
- Tamper evidence: at signing time the exact signed contract text is hashed (SHA-256) and stored with the project, together with the signer's name and timestamp — proving *what* was signed, not just *that* something was signed.
- Signing flips `contract_ack` to 1 (a signed contract IS the acknowledgment), with `contract_ack_at` = signing time. The request token is invalidated after use; re-requesting while a request is pending returns the same link.
- `invoiceguard project sign-status <id>` shows pending/signed state, signer, timestamp, and the contract hash. Dashboard signing receipt page shows the captured signature and the hash.

### Changed

- Contract §4 (Sign-off) now describes the electronic signature process instead of the v1 deposit-link acknowledgment. `project ack` (manual acknowledgment) is kept as a fallback path.

### Honest scope (e-signature)

- This is a browser-based typed/drawn signature captured by InvoiceGuard and stored locally — NOT a qualified third-party e-signature service (DocuSign/HelloSign). Legal weight varies by jurisdiction.

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
