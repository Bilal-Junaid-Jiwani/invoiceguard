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

## Cron setup

```cron
0 9 * * * /path/to/venv/bin/invoiceguard check-due >> ~/.invoiceguard/check-due.log 2>&1
```

`check-due` prints `nothing due — no emails sent` when there's nothing to do, or one line per email sent (`sent day7 to billing@acme.com (invoice #3)`).

## Email templates

`~/.invoiceguard/templates/day1.md`, `day7.md`, `day15.md` — plain markdown with `{variables}`, rendered with stdlib string formatting only:

`{client_name}` `{project_title}` `{amount}` `{paid}` `{outstanding}` `{due_date}` `{days_overdue}` `{late_fee_pct}` `{late_fee_due}` `{total_with_late_fees}` `{pay_url}`

`{amount}` is the original invoice amount; `{paid}` and `{outstanding}` come from the payment ledger (v0.2.0). `{late_fee_due}` and `{total_with_late_fees}` (v0.4.0) come from the late-fee accrual calculator, computed off the current outstanding balance.

Edit them freely; `check-due` picks up changes on the next run. If a template references an unknown variable, the run fails loudly instead of sending a broken email. If a template file is missing, the command tells you to re-run `invoiceguard init`.

Default subjects:

- day1: `Quick nudge: invoice for {project_title} is due`
- day7: `Reminder: invoice for {project_title} is 7 days overdue`
- day15: `Final notice: {project_title} invoice {days_overdue} days overdue`

## Requirements

`check-due` refuses to run until SMTP is configured — the template default host (`smtp.example.com`) is rejected with an error pointing at your config file. Email is sent through **your** SMTP provider; deliverability is yours (see [Troubleshooting](troubleshooting.html)).

> [!NOTE]
> v1 escalation is email-only. SMS/WhatsApp escalation is on the [roadmap](roadmap.html).
