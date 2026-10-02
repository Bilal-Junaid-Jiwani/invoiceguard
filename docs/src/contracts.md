---
title: Contracts
eyebrow: User guide
description: The InvoiceGuard contract generator — late-fee clause, deposit acknowledgment model, and the honest limits of v1.
---
`invoiceguard project create` generates a contract as markdown and stores it with the project. The contract is the legal backbone of the whole flow: it contains the late-fee clause that the day-15 dunning email quotes.

## What's in the contract

- **Parties and scope** — client name/email, project title, date.
- **Fee and deposit** — total fee, deposit percentage and amount. The deposit is collected through a Stripe payment link, and *paying the deposit link is the client's acknowledgment of the agreement*.
- **Late-fee clause** — if an invoice is unpaid after the grace period, a late fee of `late_fee_pct`% per month applies on the overdue balance, compounding monthly until paid.
- **Sign-off (v1)** — records `contract_ack`: `1` once the deposit link is paid.

Defaults: 50% deposit, 1.5%/month late fee, 15-day grace — all adjustable per project with `--deposit-pct`, `--late-fee-pct`, `--late-fee-grace-days`.

## The acknowledgment model

v1's "signature" is a client-acknowledgment checkbox, recorded as `contract_ack = 1` with a timestamp (`contract_ack_at`):

- Pass `--ack` to `project create` when the client has already accepted the deposit link.
- Or record it later: `invoiceguard project ack <id>`.

`project list` shows `ack` / `no-ack` per project so you can see at a glance which contracts are acknowledged.

## Honest limits

- The acknowledgment is **not a legal e-signature** — it's a record that the client paid the deposit link, which the contract text defines as acceptance. Real e-signature (typed name / drawn signature) is on the [roadmap](roadmap.html).
- Late-fee enforceability varies by jurisdiction. The clause is a contractual starting point, not legal advice. Adjust the percentage and grace days per project.
