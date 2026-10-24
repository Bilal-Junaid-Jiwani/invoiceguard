---
title: CLI reference
eyebrow: User guide
description: Every InvoiceGuard command and option — init, client, project, invoice, check-due, dashboard.
---
The CLI entry point is `invoiceguard` (installed by pip). All commands below are from v0.10.0 — run `invoiceguard --help` or `invoiceguard <group> --help` for the same text locally.

## `init`

```bash
invoiceguard init
```

Creates `~/.invoiceguard/`: the config template, an empty SQLite database, and the three dunning email templates. Idempotent — existing files are left alone.

## `client`

```bash
invoiceguard client add --name "Acme Corp" --email "billing@acme.com" [--phone +15551234567]
invoiceguard client set-phone 1 [--phone +15551234567]   # omit --phone to clear
invoiceguard client set-email 1 [--email billing@acme.com]     # omit --email to clear
invoiceguard client set-dunning-policy 1 [--day1 N] [--day7 N] [--day15 N] [--reset]
invoiceguard client list
```

- `add` requires `--name`; `--email` is optional (needed later for dunning emails). `--phone` (E.164 form) enables SMS/WhatsApp escalation for that client; invalid numbers are rejected.
- `set-phone` updates or clears the phone number of an existing client.
- `set-email` updates or clears the email address of an existing client (dunning).
- `set-dunning-policy` sets per-client dunning stage thresholds (v0.7.0, agency mode): each `--dayN` is the days-overdue threshold for that stage; `0` disables the stage for that client; omit a flag to leave it unchanged. Thresholds must stay in escalation order (day1 → day7 → day15); negative values are rejected. `--reset` clears overrides back to the global defaults (1/7/15).
- `list` prints `#id  name  email  phone` rows, plus a `[dunning ...]` marker for clients with policy overrides.

## `freelancer` (v0.8.0, agency mode)

```bash
invoiceguard freelancer add --name "Ali R." [--email ali@example.com] [--phone +15551234567] [--split-pct 70]
invoiceguard freelancer set-email 1 [--email ali@example.com]   # omit --email to clear
invoiceguard freelancer set-phone 1 [--phone +15551234567]     # omit --phone to clear
invoiceguard freelancer set-split 1 --split-pct 70   # v0.11.0
invoiceguard freelancer list
invoiceguard freelancer show 1          # v0.9.0
```

- The agency roster: one row per team member. `add` requires `--name`; `--phone` must be E.164 (invalid numbers are rejected).
- `list` prints `#id  name  email  phone` rows.
- `show` (v0.9.0; payouts added in v0.10.0, revenue splits in v0.11.0) prints contact details, per-currency earnings — billed (attributed invoices in a real state; drafts and voids excluded), collected (payment-ledger sums), outstanding — plus paid out and still owed from the payout ledger, the attributed invoice lines with collected/outstanding per invoice, and the recorded payouts. When a revenue split is set it also prints the freelancer's expected share of each collected currency and what is still due to them after payouts. Read-only.
- `--split-pct` (v0.11.0) records the freelancer's revenue-split percentage of *collected* earnings (0–100, validated before write); `freelancer set-split` changes it later. NULL (unset) means the freelancer is earnings bookkeeping only. Bookkeeping only — the split is reported, never transferred, and the payout guard is unchanged.
- Invoices are attributed to a freelancer with `invoice assign` (see below) — attribution is bookkeeping only and never changes dunning, payments, or invoice status.

## `payout` (v0.10.0, agency mode)

```bash
invoiceguard payout record --freelancer 1 --amount 250 [--currency USD] \
    [--paid-at 2026-10-23] [--method "bank transfer"] [--note "Sept share"]
invoiceguard payout list [--freelancer 1]
```

- Records what the agency sent a freelancer — bookkeeping only, nothing is transferred. A payout is refused with a clear error when it exceeds what the agency has collected for that freelancer in that currency (payment-ledger sums against their attributed non-draft/void invoices, minus payouts already recorded); you cannot pay out money you have not received. Currency codes are normalized to upper-case; invoice-currency matching is case-insensitive; `--paid-at` accepts `YYYY-MM-DD` (default: now).
- `list` prints `#id  name  currency  amount  date  method  note` rows, newest first; `--freelancer ID` restricts to one freelancer. Revenue splits shipped in v0.11.0 (`freelancer set-split`); per-freelancer payout schedules remain unshipped.

## `project`

```bash
invoiceguard project create --client "Acme Corp" --title "Website redesign" \
  --amount 2000 --currency USD --deposit-pct 50 \
  --late-fee-pct 1.5 --late-fee-grace-days 15 [--ack]
invoiceguard project list
invoiceguard project ack 1
invoiceguard project sign-request 1   # one-time e-signature link
invoiceguard project sign-status 1    # pending / signed + signer + hash
```

- `create` requires an existing client (matched by exact `--client` name) and `--title` and `--amount` (in currency units, e.g. `2000`).
- Defaults: `--currency USD`, `--deposit-pct 50`, `--late-fee-pct 1.5`, `--late-fee-grace-days 15`.
- Validation: `--deposit-pct` and `--late-fee-pct` must be between 0 and 100; `--late-fee-grace-days` must be 0 or more. Out-of-range values are refused before anything is written (the percentages also land in the generated contract text).
- Prints the generated contract (with the late-fee clause) as a preview.
- `--ack` records the contract as acknowledged by the client at creation time; without it, the contract shows `no-ack` until you run `project ack ID` after the client accepts the deposit link.
- `list` shows `#id  title  client  amount  [ack|no-ack]`.
- `sign-request` creates a one-time, single-use signing link (`/sign/<token>`, served by the dashboard) for the client to type their name and draw a signature; the contract text is frozen at link-creation time and its SHA-256 is stored as tamper evidence. Re-running while a request is pending returns the same link (with its original frozen text). `sign-status` shows both tracks: the e-signature state *and* the deposit-link acknowledgment (`project ack` / `--ack`) — an acknowledged project with a signature still outstanding says so, instead of only reporting "awaiting signature" (0.8.4).

See [Contracts](contracts.html) for what the contract contains and the honest limits of the e-signature.

## `invoice`

```bash
invoiceguard invoice create --project 1 --kind deposit [--amount 1000] [--due-days 7]
invoiceguard invoice list [--freelancer 1]
invoiceguard invoice assign 1 --freelancer 1   # or: --none to clear
invoiceguard invoice record-payment 1 --amount 250 [--note "check #1"] [--method bank]
invoiceguard invoice mark-paid 1
invoiceguard invoice void 1
invoiceguard invoice late-fees 1 [--as-of 2026-10-06]
invoiceguard invoice notify 1 [--channel sms|whatsapp] [--dry-run]
invoiceguard invoice report [--as-of 2026-10-08] [--freelancer 1] [--csv invoices.csv] [--payments-csv payments.csv]
```

- `create` requires `--project` (id) and `--kind`: one of `deposit`, `milestone`, `final`.
- Amount defaults by kind: `deposit` = `deposit_pct`% of the project total; `final` = the remainder after the deposit; `milestone` = the full project amount. `--amount` overrides in currency units.
- `--due-days` defaults to 7; `0` or negative means "due today".
- On success it creates a real Stripe payment link (test mode friendly), stores the URL, and sets status to `sent`. If the Stripe secret key is missing or still the placeholder, the command fails with a clear error — no invoice is left half-created.
- `list` shows `#id  kind  status  amount  project  [freelancer]  due-date`; `--freelancer ID` shows only that freelancer's invoices.
- `assign` (v0.8.0, agency mode) attributes an invoice to a freelancer (`--freelancer ID`), or clears the attribution with `--none`. Pure bookkeeping: dunning, payments, and status are untouched.
- `record-payment` records a (partial) payment in the ledger. A payment that clears the outstanding balance marks the invoice `paid`; otherwise the invoice becomes `partially-paid` and keeps dunning against the remaining balance. Overpaying is rejected.
- `mark-paid` manually marks an invoice paid — since v0.2.0 it records the full outstanding balance in the ledger, and it errors on already-paid or void invoices. `void` voids an unpaid invoice; a **paid** invoice cannot be voided (0.8.4 — its ledger payments stand; refund through Stripe instead), and voiding an already-void invoice errors too.
- `late-fees` (v0.4.0) shows the accrued late fees for an invoice under contract §3: due date, grace window, monthly rate, months billed, accrued fees, and total due. One fee is billed per **full monthly period elapsed** since accrual started (0.8.4 — see the changelog for why started-calendar-month counting was wrong). `--as-of YYYY-MM-DD` computes as of a past date (default: today).
- `notify` (v0.5.0) sends an SMS/WhatsApp payment reminder for one invoice right now (same text as the automatic day-15 message). `--dry-run` prints the rendered message without sending — no Twilio credentials needed, but the client needs a phone number.
- `report` (v0.6.0) is a read-only receivables report: open invoices (sent / overdue / partially-paid with a balance) grouped into Current / 1-30 / 31-60 / 61-90 / 90+ day aging buckets, oldest debt first, with accrued late fees, per-client totals, and grand totals. `--as-of YYYY-MM-DD` pins the report date. `--csv PATH` exports one row per open invoice and `--payments-csv PATH` exports the full payment ledger for accounting; `-` as the path prints that CSV to stdout instead of the text report. `--freelancer ID` (v0.8.0) restricts the report to one freelancer's invoices; the text report also prints a "by freelancer" breakdown when more than one attribution group has open invoices. It never sends anything or changes invoice state. CSV exports (v0.8.1) neutralize spreadsheet formula injection: any exported cell starting with `=`, `+`, `-`, `@`, `|`, or `%` is prefixed with a single quote so it opens as plain text (OWASP CSV-injection guidance).

## `check-due`

```bash
invoiceguard check-due
```

Scans for overdue invoices (`sent`/`overdue` status, due date in the past) and sends the next dunning stage. Refuses to run until SMTP is configured. Meant for cron — see [Dunning engine](dunning.html).

## `dashboard`

```bash
invoiceguard dashboard [--port 8000] [--host 127.0.0.1]
```

Launches the localhost web UI (uvicorn serving the FastAPI app). Binds to `127.0.0.1` by default — keep it that way unless you know what you're doing. See [Dashboard](dashboard.html).

## Exit behavior

Errors print a one-line message (`Error: ...`) and exit non-zero. Destructive operations (`void`, `mark-paid`) act on explicit ids only — there is no bulk mode in v0.8.4.
