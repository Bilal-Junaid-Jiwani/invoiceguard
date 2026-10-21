# Changelog

Every release, newest first. InvoiceGuard follows semver.

## [0.8.3] - 2026-10-21

E-signature contract snapshot: the signing flow now freezes the contract text at the moment the link is created.

### Fixed
- Verified: `sign-request` hashed and rendered the project's *live* contract text — so if the contract was edited after the signing link was created but before the client signed, the SHA-256 tamper-evidence hash covered text the client never saw (and the signing page itself showed the edited text). The project's contract markdown is now snapshotted into the signature request row at creation time (`signatures.contract_md`); the signing page renders that snapshot and the signature hash covers it. Requests created before v0.8.3 have no snapshot and fall back to the current project text; existing databases get the new column additively on connect (closes the e-signature TOCTOU audit finding).

### Honest scope (0.8.3)
- Snapshot-side only: nothing about token lifecycle, signature capture, or acknowledgment semantics changed. Re-running `sign-request` while a request is pending still returns the existing link with its original snapshot.

## [0.8.2] - 2026-10-20

`project create` now validates its financial options: `--deposit-pct` and `--late-fee-pct` must be within 0–100, and `--late-fee-grace-days` must be 0 or more. Out-of-range values are refused with a clear error before anything is written.

### Fixed
- The percentages flow into the stored project *and* the generated contract text, so values like `--deposit-pct 500` or `--late-fee-pct -3` previously produced nonsense money math and a nonsense late-fee clause in a legal document. They are now rejected up front (closes the remaining CLI financial-validation audit finding; `--amount` already validated, dunning thresholds already validated).

### Honest scope (0.8.2)
- Validation is CLI-side only: projects created by older versions with odd values are untouched (no migration, no rewrite of stored data).

## [0.8.1] - 2026-10-17

CSV formula-injection fix: the `--csv` and `--payments-csv` exports now neutralize spreadsheet formula injection (OWASP).

### Fixed
- Verified: client names, project titles, payment methods, and payment notes are free text, and the invoice/payment CSV exports wrote them raw — a value like `=HYPERLINK("http://evil","x")` in a client name would execute as a live formula when the accountant opened the export in Excel/LibreOffice (classic CSV injection). Any exported cell whose first character is `=`, `+`, `-`, `@`, `|`, `%`, tab, or carriage return now gets a leading single quote (`'=HYPERLINK(...)`) — spreadsheet apps render it as plain text, so benign values are unchanged and only the formula interpretation is defused.

### Honest scope (0.8.1)
- Export-side only: the database, CLI parsing, and every other output (text report, dashboard) are untouched. The quote is a text marker that spreadsheet apps do not display; raw CSV readers see the literal leading `'`.

## [0.8.0] - 2026-10-16

Freelancer roster + invoice attribution — the second agency-mode slice.

### Added
- `freelancers` table: `invoiceguard freelancer add --name N [--email E] [--phone P]`, `list`, `set-email`, `set-phone` (mirrors the `client` commands; phone must be E.164).
- `invoiceguard invoice assign <id> --freelancer <id>` attributes an invoice to a freelancer; `--none` clears it. Attribution is pure bookkeeping — it never changes dunning, payments, or invoice status.
- `invoice list --freelancer ID` filters to one freelancer's invoices; the list shows the attributed freelancer's name.
- `invoice report --freelancer ID` restricts the receivables report to one freelancer; the text report gains a "by freelancer" breakdown (shown when more than one attribution group has open invoices); the invoice CSV gains a trailing `freelancer` column.
- The dashboard shows the attributed freelancer on the invoice list and the invoice detail page.
- Existing databases get the `freelancers` table and `invoices.freelancer_id` additively on connect (NULL = unattributed, no data touched).

### Honest scope (0.8.0)
- No per-freelancer logins, permissions, or separate workspaces: attribution records *whose* work an invoice is so receivables can be broken down per freelancer. The remaining half of agency mode (multi-freelancer workspaces) is still unshipped.

## [0.7.1] - 2026-10-15

Stripe webhook idempotency: processed Stripe event ids are now recorded in a `webhook_events` table, so a retried delivery of an already-handled event is ignored (`ignored:duplicate_event`) instead of re-recorded.

### Fixed
- A retried `checkout.session.completed` webhook for a *partial* payment recorded a second payment row — replaying the same half-payment event could mark the invoice fully-paid on half the money. Same event id delivered twice is now a no-op on the second delivery; distinct event ids for the same invoice are still processed normally.
- Existing databases get the `webhook_events` table additively on connect (no manual migration).

## [0.7.0] - 2026-10-14

Per-client dunning policies: the first agency-mode slice — each client can now have its own dunning stage thresholds.

### Added
- `clients` table gains `dunning_day1`, `dunning_day7`, `dunning_day15` (additive migration — existing databases get the columns on connect; NULL = global default, 0 = stage disabled for that client).
- `invoiceguard client set-dunning-policy <id> [--day1 N] [--day7 N] [--day15 N] [--reset]` — set per-client days-overdue thresholds; `0` disables a stage for that client; `--reset` restores the global defaults (1/7/15). Thresholds must keep escalation order (day1 -> day7 -> day15); negative values are rejected and nothing is written on validation failure.
- `check-due` resolves each invoice's client policy per invoice: custom thresholds fire at the custom day, disabled stages are skipped in escalation, and an invoice whose client has every stage disabled is reported as `skipped dunning (invoice #N)` — nothing recorded, so the opt-out stays visible instead of silently vanishing.
- `client list` prints a `[dunning ...]` marker for clients with overrides (e.g. `[dunning day1=3 day15=off]`).

### Honest scope (0.7.0)
- Only *when* (or whether) a stage fires changes: stage names, subjects, templates, and idempotency are untouched. The day-15 SMS/WhatsApp escalation still follows the `day15` stage — with a custom threshold it fires at the custom day. Multi-freelancer workspaces (the other half of agency mode) are still unshipped.

## [0.6.5] - 2026-10-13

Dunning send-failure isolation: one invoice's email send failing no longer aborts the whole `check-due` run.

### Fixed
- `check_due` called `send_email()` unguarded inside the per-invoice loop — verified: a transient SMTP failure (`smtplib.SMTPException`) on the first overdue invoice propagated out and aborted the run, so every *other* overdue invoice never got its stage that run. This is the same "one bad invoice kills the collections run" shape as the email-less-client crash fixed in 0.6.3 (different trigger). Sending is now isolated per invoice: a failed send is reported in the result dict as `send_error` (and printed as `failed <stage> (invoice #N): ...` by the CLI), nothing is recorded for that invoice, and the rest of the batch still goes out. Catches `OSError` (SMTP, socket, and SSL errors) plus `ValueError`.

### Honest scope (0.6.5)
- Nothing is recorded for a failed-send invoice — no `dunning_events` row and no status flip to `overdue` — so the stage is simply retried on the next run (the same convention as the 0.6.3 email-less skip). Successful sends are unchanged: send → record → flip, one email per invoice per run, stages still idempotent.

## [0.6.4] - 2026-10-12

Webhook void-safety: a Stripe webhook no longer resurrects a voided invoice.

### Fixed
- The dashboard's `mark_invoice_paid` (the `POST /webhooks/stripe` handler) recorded the Stripe payment and marked the invoice `paid` even when the invoice had been voided — verified: a void invoice came back as `paid` with a `stripe` ledger row. The core `record_payment()` refuses payments on void invoices (`ValueError: invoice #N is void`), so a late or duplicate webhook after `invoice void` resurrected the invoice against the rest of the system's rules. Void is now terminal on the webhook path too: the event is ignored, nothing is recorded, and the invoice stays void.

### Honest scope (0.6.4)
- Only the webhook path changed: sent / partially-paid / overdue invoices are marked paid exactly as before, and already-paid invoices stay idempotent. If Stripe money actually lands against a voided invoice, it is not recorded in InvoiceGuard — void it only when you mean no payment should count.

## [0.6.3] - 2026-10-11

Dunning robustness: one email-less client no longer kills the whole collections run.

### Fixed
- `check-due` crashed with `ValueError: recipient address is empty` when any overdue invoice's client had no email on file — and since `client add` makes `--email` optional, this was easy to hit. The exception aborted the entire run, so every *other* overdue invoice never got its email. Overdue invoices with no client email are now skipped with a reported reason (`skipped day7 (invoice #3): client has no email on file — add one with `invoiceguard client set-email``); nothing is recorded for a skipped invoice, so the stage goes out on the next run once an email is on file, and the rest of the batch is unaffected.

### Added
- `invoiceguard client set-email <id> [--email ...]` — set or clear a client's email address (dunning). Previously there was no way to add an email to a client created without one (`set-phone` existed; `set-email` did not).

### Honest scope (0.6.3)
- Only the email path changes: a skipped stage means its email never went out, so neither the day-15 SMS/WhatsApp escalation (which is only ever *in addition* to the email) nor any dunning-event row is recorded for it. Invoice state is otherwise untouched; existing databases need no migration. Agency mode remains unshipped.

## [0.6.2] - 2026-10-10

Contract deposit fix: the amount printed in the contract now always matches the deposit you are quoted and charged.

### Fixed
- `render_contract` computed the deposit shown in the contract with binary float + Python's `round()` (banker's rounding) — so a $10.05 project at 50% produced a contract saying a **$5.02** deposit was due, while `project create` quoted $5.03 and the deposit invoice charged $5.03. The contract is the document the client signs (and whose exact text is SHA-256-hashed as tamper evidence at signing), so its deposit figure must be the charged one. It now uses the project's money convention — `Decimal`, rounded half-up to the cent — the same rule the CLI amount parsing (0.6.1) and the late-fee calculator already used.

### Honest scope (0.6.2)
- Only the deposit figure in newly generated contract text changes, and only on half-cent boundaries (odd-cent totals at a 50% split, etc.) where the old rounding disagreed. Stored contracts, invoice amounts, and the payment ledger are untouched; existing databases need no migration. Agency mode remains unshipped.

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
