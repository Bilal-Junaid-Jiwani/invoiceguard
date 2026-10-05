---
title: Troubleshooting
eyebrow: Reference
description: Fix common InvoiceGuard problems — config, Stripe, SMTP, webhooks, templates — plus the honest limits of v1.
---
## `invoice create` fails: "Stripe secret key is not configured"

Set `stripe_secret_key` in `~/.invoiceguard/config.yaml` or export `INVOICEGUARD_STRIPE_SECRET_KEY`. Values that still look like the template placeholder (`sk_test_...`) are rejected on purpose. Use a real test key from <https://dashboard.stripe.com/test/apikeys>.

## Webhook returns 500: "Stripe webhook secret is not configured"

The dashboard couldn't find a real signing secret. Check the resolution order in [Webhooks](webhooks.html): `INVOICEGUARD_STRIPE_WEBHOOK_SECRET` env → `stripe_webhook_secret` in the config → legacy `STRIPE_WEBHOOK_SECRET` env. A `whsec_...` placeholder counts as unconfigured — paste the real signing secret from Stripe dashboard → Developers → Webhooks.

## Webhook returns 400: "invalid signature"

The payload's `Stripe-Signature` header doesn't match your secret. Common causes: the secret belongs to a different webhook endpoint, or a proxy/load balancer modified the raw request body. The endpoint must receive the exact bytes Stripe sent.

## `check-due` refuses to run: SMTP not configured

Set `smtp.host` (and the rest of `smtp.*`) in the config — the `smtp.example.com` template default is rejected deliberately. Gmail users need an App Password, not the account password.

## Emails land in spam

Email is sent through *your* SMTP provider — deliverability is yours. Use a reputable provider, warm up new sending addresses, and check spam folders during testing. InvoiceGuard can't fix a provider's reputation for you.

## "dunning template missing ... (re-run `invoiceguard init`)"

A `~/.invoiceguard/templates/dayN.md` file was deleted. Re-run `invoiceguard init` to restore the defaults (existing files are left alone), or recreate the file yourself.

## Template renders a broken email

If a template references an unknown `{variable}`, `check-due` fails loudly instead of sending. The valid variables are listed in [Dunning engine](dunning.html): `{client_name}` `{project_title}` `{amount}` `{due_date}` `{days_overdue}` `{late_fee_pct}` `{pay_url}`.

## Dashboard shows an empty invoice list

It's reading a different database than your CLI. Check `INVOICEGUARD_DB` in the dashboard's environment — the default is `~/.invoiceguard/invoiceguard.db` for both.

## Paid invoice still shows as "sent"

Either the webhook isn't configured (see above), the event wasn't `checkout.session.completed`, or the payment link's metadata doesn't carry the invoice id (links created by v0.1.0 always do). Use `invoiceguard invoice mark-paid <id>` as the manual fallback.

## Limits of v1

Honest boundaries, not bugs:

- **No live Stripe call was verified in this build environment.** Payment-link creation was verified with a mocked Stripe SDK (exact API params asserted); webhook handling was verified with real `stripe.Webhook.construct_event` signature checks offline. You add your own test key; the live path is the standard Stripe API.
- **The contract "signature" is browser-captured, not qualified.** v0.3.0 added real e-signature: the client types their name and draws a signature on the signing page; the signed text is hashed (SHA-256) and stored as tamper evidence. It is not a qualified third-party e-signature service; legal weight varies by jurisdiction. The older acknowledgment path (paying the deposit link = accepting the terms; `contract_ack=1`) still works as a fallback.
- **Email deliverability is yours** (your SMTP, your reputation).
- **Late-fee enforceability varies by jurisdiction** — the clause is a starting point, not legal advice.
