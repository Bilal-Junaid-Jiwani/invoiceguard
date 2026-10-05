---
title: Contracts
eyebrow: User guide
description: The InvoiceGuard contract generator — late-fee clause, deposit acknowledgment model, and the honest limits of v1.
---
`invoiceguard project create` generates a contract as markdown and stores it with the project. The contract is the legal backbone of the whole flow: it contains the late-fee clause that the day-15 dunning email quotes.

## What's in the contract

- **Parties and scope** — client name/email, project title, date.
- **Fee and deposit** — total fee, deposit percentage and amount. The deposit is collected through a Stripe payment link.
- **Late-fee clause** — if an invoice is unpaid after the grace period, a late fee of `late_fee_pct`% per month applies on the overdue balance, compounding monthly until paid.
- **Sign-off** — the client signs electronically (typed name + drawn signature); the exact signed text is hashed (SHA-256) and stored as tamper evidence.

Defaults: 50% deposit, 1.5%/month late fee, 15-day grace — all adjustable per project with `--deposit-pct`, `--late-fee-pct`, `--late-fee-grace-days`.

## E-signature

Since v0.3.0, the client can sign the contract electronically instead of (or in addition to) acknowledging via the deposit link:

```bash
invoiceguard project sign-request 3     # prints a one-time signing link
# send the link to the client while `invoiceguard dashboard` is running
invoiceguard project sign-status 3      # pending / signed + signer + hash
```

The signing page (`/sign/<token>`) renders the contract, and the client types their full name and draws a signature (mouse or touch). On submit, InvoiceGuard stores:

- the signer's typed name,
- the drawn signature image (PNG data URL),
- the **SHA-256 of the exact contract text at signing time** — tamper evidence proving *what* was signed,
- the signing timestamp.

Signing flips `contract_ack` to `1` (a signed contract IS the acknowledgment) with `contract_ack_at` = signing time. The link is single-use: after signing it shows a receipt instead of the form. Re-running `sign-request` while a request is still pending returns the same link rather than orphaning it.

## The older acknowledgment path

Before e-signature, acknowledgment was recorded when the deposit link was paid (or manually with `project ack` / `--ack` on create). That path still works: `invoiceguard project ack <id>` records the acknowledgment with a timestamp. `project list` shows `ack` / `no-ack` per project.

## Honest limits

- The e-signature is a **browser-captured typed/drawn signature stored locally** with a tamper-evidence hash — it is NOT a qualified third-party e-signature service (DocuSign/HelloSign). Legal weight varies by jurisdiction.
- Late-fee enforceability varies by jurisdiction. The clause is a contractual starting point, not legal advice. Adjust the percentage and grace days per project.
