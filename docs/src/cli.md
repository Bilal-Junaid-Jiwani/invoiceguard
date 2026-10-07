---
title: CLI reference
eyebrow: User guide
description: Every InvoiceGuard command and option — init, client, project, invoice, check-due, dashboard.
---
The CLI entry point is `invoiceguard` (installed by pip). All commands below are from v0.1.0 — run `invoiceguard --help` or `invoiceguard <group> --help` for the same text locally.

## `init`

```bash
invoiceguard init
```

Creates `~/.invoiceguard/`: the config template, an empty SQLite database, and the three dunning email templates. Idempotent — existing files are left alone.

## `client`

```bash
invoiceguard client add --name "Acme Corp" --email "billing@acme.com" [--phone +15551234567]
invoiceguard client set-phone 1 [--phone +15551234567]   # omit --phone to clear
invoiceguard client list
```

- `add` requires `--name`; `--email` is optional (needed later for dunning emails). `--phone` (E.164 form) enables SMS/WhatsApp escalation for that client; invalid numbers are rejected.
- `set-phone` updates or clears the phone number of an existing client.
- `list` prints `#id  name  email  phone` rows.

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
- Prints the generated contract (with the late-fee clause) as a preview.
- `--ack` records the contract as acknowledged by the client at creation time; without it, the contract shows `no-ack` until you run `project ack ID` after the client accepts the deposit link.
- `list` shows `#id  title  client  amount  [ack|no-ack]`.
- `sign-request` creates a one-time, single-use signing link (`/sign/<token>`, served by the dashboard) for the client to type their name and draw a signature; the signed contract text is hashed (SHA-256) and stored as tamper evidence. Re-running while a request is pending returns the same link. `sign-status` shows the request state.

See [Contracts](contracts.html) for what the contract contains and the honest limits of the e-signature.

## `invoice`

```bash
invoiceguard invoice create --project 1 --kind deposit [--amount 1000] [--due-days 7]
invoiceguard invoice list
invoiceguard invoice record-payment 1 --amount 250 [--note "check #1"] [--method bank]
invoiceguard invoice mark-paid 1
invoiceguard invoice void 1
invoiceguard invoice late-fees 1 [--as-of 2026-10-06]
invoiceguard invoice notify 1 [--channel sms|whatsapp] [--dry-run]
```

- `create` requires `--project` (id) and `--kind`: one of `deposit`, `milestone`, `final`.
- Amount defaults by kind: `deposit` = `deposit_pct`% of the project total; `final` = the remainder after the deposit; `milestone` = the full project amount. `--amount` overrides in currency units.
- `--due-days` defaults to 7; `0` or negative means "due today".
- On success it creates a real Stripe payment link (test mode friendly), stores the URL, and sets status to `sent`. If the Stripe secret key is missing or still the placeholder, the command fails with a clear error — no invoice is left half-created.
- `list` shows `#id  kind  status  amount  project  due-date`.
- `record-payment` records a (partial) payment in the ledger. A payment that clears the outstanding balance marks the invoice `paid`; otherwise the invoice becomes `partially-paid` and keeps dunning against the remaining balance. Overpaying is rejected.
- `mark-paid` manually marks an invoice paid — since v0.2.0 it records the full outstanding balance in the ledger, and it errors on already-paid or void invoices. `void` voids it.
- `late-fees` (v0.4.0) shows the accrued late fees for an invoice under contract §3: due date, grace window, monthly rate, months billed, accrued fees, and total due. `--as-of YYYY-MM-DD` computes as of a past date (default: today).
- `notify` (v0.5.0) sends an SMS/WhatsApp payment reminder for one invoice right now (same text as the automatic day-15 message). `--dry-run` prints the rendered message without sending — no Twilio credentials needed, but the client needs a phone number.

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

Errors print a one-line message (`Error: ...`) and exit non-zero. Destructive operations (`void`, `mark-paid`) act on explicit ids only — there is no bulk mode in v0.1.0.
