# Changelog

Every release, newest first. InvoiceGuard follows semver.

## [0.6.1] - 2026-10-09

CLI money-parsing fix: amounts you type are no longer a cent low.

### Fixed
- `project create --amount`, `invoice create --amount`, and `invoice record-payment --amount` parsed your input through binary float + Python's `round()` (banker's rounding), so `--amount 1.005` was recorded as 100 cents instead of 101 and `record-payment --amount 10.075` booked 1007 instead of 1008. Amounts are now parsed with `Decimal` and rounded half-up to the cent — the same rounding convention the late-fee calculator (contract §3) already documented — and the deposit/final split uses the same half-up rule (deposit + final still sum to the project total).
- Non-positive or non-numeric amounts (`--amount 0`, `--amount -50`, `--amount abc`) are now rejected with a clear error before anything is written; previously `project create --amount -50` silently created a negative-cents project, and a bad `invoice create --amount` could leave an orphan draft invoice behind when Stripe rejected the amount.

### Honest scope (0.6.1)
- Only CLI-entered amounts changed; stored cents, the payment ledger, and Stripe session amounts were already exact integers and are untouched. Existing databases need no migration. Agency mode remains unshipped.

## [0.6.0] - 2026-10-08

Receivables report: see who owes you what, and how late — without opening the dashboard — and get the numbers out as CSV for your accountant.

### Added
- `invoiceguard invoice report [--as-of YYYY-MM-DD]` — read-only aging report over open invoices (sent / overdue / partially-paid with a balance): outstanding totals grouped into Current / 1-30 / 31-60 / 61-90 / 90+ day buckets, every open invoice oldest-debt-first with its accrued late fees (same contract §3 calculator as `invoice late-fees`), per-client totals, and grand totals. "Open" and "outstanding" mean exactly what the dashboard totals mean (invoice amount minus the payment-ledger sum); drafts, paid, and void invoices are excluded. Totals are per currency — different currencies are never added together.
- CSV export for accounting: `--csv PATH` writes one row per open invoice (amount, paid, outstanding, accrued late fees, total due, due date, days overdue); `--payments-csv PATH` writes the full payment ledger (date, invoice, client, method, amount, note) for bank reconciliation — including payments on invoices that are now paid in full. Passing `-` as the path prints that CSV to stdout instead of the text report.

### Honest scope (report)
- Read-only: the report never sends anything and never changes invoice state. Late fees are computed as of the report date on the current outstanding balance — the same caveat as `invoice late-fees` (no day-by-day amortization of payments made mid-accrual). Aging buckets are whole days overdue as of the report date; an invoice due today counts as Current. Agency mode (multi-freelancer workspaces, per-client dunning policies) remains unshipped — it is a multi-day change and was deliberately not rushed into this release.

## [0.5.0] - 2026-10-07

SMS / WhatsApp escalation: the day-15 formal notice can now also reach the client as a short message, not just email — clients read texts even when they let email pile up.

### Added
- `messaging.py`: SMS and WhatsApp sending via the Twilio Messages API (stdlib urllib, no new dependency). Same context variables as the dunning emails, rendered into one short message (`InvoiceGuard: Hi {client_name}, ... Pay here: {pay_url}`).
- Automatic escalation: when `messaging:` is configured and the client has a phone on file, `check-due` sends the day-15 stage by message as well as email. Sends are recorded in a new `message_events` ledger and happen at most once per stage per invoice (same idempotency rule as email). A failed message never blocks the email — the failure is reported in the `check-due` output instead.
- `invoiceguard invoice notify <id> [--channel sms|whatsapp] [--dry-run]` — send a payment reminder for one invoice right now; `--dry-run` renders and prints the exact message without sending (works without Twilio credentials).
- Client phone numbers: `invoiceguard client add --phone +15551234567`, `invoiceguard client set-phone <id> [--phone ...]`, shown in `client list`. Numbers are normalized to E.164 form and validated on entry.
- Config: new `messaging:` section (`channel`, `account_sid`, `auth_token`, `from_number`), overridable via `INVOICEGUARD_TWILIO_ACCOUNT_SID` / `INVOICEGUARD_TWILIO_AUTH_TOKEN` / `INVOICEGUARD_TWILIO_FROM_NUMBER` env vars. Additive DB migration adds `clients.phone` + the `message_events` table to existing databases automatically.

### Honest scope (messaging)
- Requires your own Twilio account (credentials + a Twilio number); sends are verified with the HTTP layer mocked — no live Twilio call was made in this build environment. WhatsApp additionally requires a Twilio-approved WhatsApp sender on your account. Message/phone-number availability and pricing are Twilio's, and consumer-messaging consent rules vary by jurisdiction — only message clients who agreed to be contacted.

## [0.4.0] - 2026-10-06

Late-fee accrual calculator: the contract has always promised "{late_fee_pct}% per month, compounding monthly" after the grace period — now InvoiceGuard computes the actual number.

### Added
- `invoiceguard invoice late-fees <id> [--as-of YYYY-MM-DD]` — shows the accrual breakdown: due date, grace window, rate, months billed, accrued fees, and total due. Formula: fees start accruing the day after the grace period ends; each started calendar month charges one full monthly fee, compounding on the running balance (outstanding + fees accrued so far), each month's fee rounded half-up to the cent.
- Dashboard invoice detail page: "Accrued late fees" and "Total with fees" rows under Contract & terms (accrues live off the current outstanding balance).
- New dunning template variables: `{late_fee_due}` and `{total_with_late_fees}` — available in all stages. The shipped `day15.md` formal-notice template now quotes the actual accrued fees and total due.

### Honest scope (late fees)
- A partial month counts as a full month (matches the contract's "compounding monthly" wording). The calculator uses the *current* outstanding balance; it does not retroactively amortize fees day-by-day for payments made mid-accrual — re-run it after recording a payment. Late-fee enforceability varies by jurisdiction.

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
- `invoiceguard invoice record-payment <id> --amount <dollars> [--note ...] [--method ...]` — record a partial payment; a payment that clears the balance marks the invoice `paid`, otherwise the invoice becomes `partially-paid`
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
