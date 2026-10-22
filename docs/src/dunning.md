---
title: Dunning engine
eyebrow: User guide
description: The automated 3-stage collections engine — day1/day7/day15 escalation, idempotency, editable templates, cron setup.
---
`invoiceguard check-due` scans for overdue invoices and sends the next escalation stage. It is designed to run unattended from cron.

## The three stages

| Stage | Fires at | Tone |
|---|---|---|
| `day1` | ≥ 1 day overdue | polite nudge |
| `day7` | ≥ 7 days overdue | firm reminder |
| `day15` | ≥ 15 days overdue | formal notice quoting the late-fee clause |

The earliest stage whose threshold is met **and that hasn't been sent yet** is sent — escalation always goes day1 → day7 → day15, and exactly one email is sent per invoice per run.

## Idempotency

Each stage is recorded in the `dunning_events` table and sent **at most once per invoice**. Re-running `check-due` never double-sends. Only invoices with status `sent`, `overdue`, or `partially-paid`, a due date in the past, and an outstanding balance above zero are considered; a sent invoice flips to `overdue` on its first dunning email. Partially-paid invoices keep escalating until the balance is zero — the emails show the paid-so-far and remaining balance (see template variables).

## Skipped invoices

An invoice whose client has **no email on file** is skipped with a reported reason instead of crashing the run:

```bash
skipped day7 (invoice #3): client has no email on file — add one with `invoiceguard client set-email`
```

Nothing is recorded for a skipped invoice, so the stage goes out normally on the next run after the email is added. The rest of the batch is unaffected — every other overdue invoice still gets its email.

## Failed sends are isolated

If one invoice's email **fails to send** (a transient SMTP error, a bad relay, ...), the failure is isolated to that invoice — it no longer aborts the whole run:

```bash
failed day1 (invoice #2): email send failed: 421 transient failure from relay — stage not recorded, will retry next run
```

Nothing is recorded for a failed invoice (no `dunning_events` row, no status flip), so the stage is simply retried on the next run, and every other overdue invoice still gets its stage.

## Per-client dunning policies (v0.7.0)

The first slice of agency mode: each client can have its own stage thresholds. A positive number sets a custom days-overdue threshold, `0` disables the stage for that client, and omitting a flag (or `--reset`) keeps the global default (1 / 7 / 15):

```bash
invoiceguard client set-dunning-policy 4 --day1 3 --day7 10 --day15 20
invoiceguard client set-dunning-policy 4 --day15 0     # never send the formal notice to this client
invoiceguard client set-dunning-policy 4 --reset       # back to the global defaults
```

Thresholds must keep escalation order — `day1 → day7 → day15` ascending — and negative values are rejected (nothing is written on validation failure). Only *when* (or whether) a stage fires changes: stage names, templates, subjects, and idempotency are untouched, and the day-15 SMS/WhatsApp escalation still follows the `day15` stage — with a custom threshold it fires at the custom day.

An overdue invoice whose client has every stage disabled is not silently dropped — `check-due` reports it so the opt-out stays visible:

```bash
skipped dunning (invoice #12): dunning disabled for this client (per-client policy — change it with `invoiceguard client set-dunning-policy`)
```

`client list` shows a `[dunning day1=3 day15=off]` marker next to clients with overrides.

## Cron setup

```cron
0 9 * * * /path/to/venv/bin/invoiceguard check-due >> ~/.invoiceguard/check-due.log 2>&1
```

`check-due` prints `nothing due — no emails sent` when there's nothing to do, or one line per email sent (`sent day7 to billing@acme.com (invoice #3)`). Skipped invoices print a `skipped` line, failed sends a `failed` line — neither is retried within the same run; both are retried on the next run.

## Email templates

`~/.invoiceguard/templates/day1.md`, `day7.md`, `day15.md` — plain markdown with `{variables}`, rendered with stdlib string formatting only:

`{client_name}` `{project_title}` `{amount}` `{paid}` `{outstanding}` `{due_date}` `{days_overdue}` `{late_fee_pct}` `{late_fee_due}` `{total_with_late_fees}` `{pay_url}`

`{amount}` is the original invoice amount; `{paid}` and `{outstanding}` come from the payment ledger (v0.2.0). `{late_fee_due}` and `{total_with_late_fees}` (v0.4.0) come from the late-fee accrual calculator, computed off the current outstanding balance.

Edit them freely; `check-due` picks up changes on the next run. If a template references an unknown variable, the run fails loudly instead of sending a broken email. If a template file is missing, the command tells you to re-run `invoiceguard init`.

Emails go out as **plain text**: markdown emphasis (`**...**`) is stripped at send time so clients never see literal asterisks, and a legacy `Subject:` first line in a template file is ignored — the subject always comes from the defaults below (0.8.4).

Default subjects:

- day1: `Quick nudge: invoice for {project_title} is due`
- day7: `Reminder: invoice for {project_title} is 7 days overdue`
- day15: `Final notice: {project_title} invoice {days_overdue} days overdue`

## Requirements

`check-due` refuses to run until SMTP is configured — the template default host (`smtp.example.com`) is rejected with an error pointing at your config file. Email is sent through **your** SMTP provider; deliverability is yours (see [Troubleshooting](troubleshooting.html)).

## SMS / WhatsApp escalation (v0.5.0)

When the `messaging:` section is configured (see [Configuration](configuration.html)) and the client has a phone number on file, the **day15** stage also goes out as a short SMS or WhatsApp message via Twilio — same outstanding amount, accrued late fees, and pay link, in one short text. Sends are recorded in the `message_events` ledger and happen **at most once per stage per invoice**, exactly like email. A failed message send never blocks the email; the failure is printed in the `check-due` output instead.

`invoiceguard invoice notify <id>` sends the same message on demand (with `--dry-run` to preview without sending). Without messaging configuration, dunning stays email-only — nothing errors, nothing pretends a message was sent.

> [!NOTE]
> Messaging requires your own Twilio account; WhatsApp additionally needs a Twilio-approved WhatsApp sender. Only message clients who agreed to be contacted — consent rules vary by jurisdiction.
