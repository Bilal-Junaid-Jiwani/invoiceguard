---
title: invoiceguard docs
eyebrow: Overview
description: InvoiceGuard documentation — freelancer payment enforcement with deposit links, late-fee contracts, and automated 3-stage collections.
---
InvoiceGuard is an open-source Python tool for freelancers who are tired of chasing late payments. Generate a contract with a late-fee clause, collect a deposit through a Stripe payment link, and let an automated 3-stage email escalation (day-1 nudge → day-7 reminder → day-15 formal notice) handle the collections. Local-first (SQLite), Apache-2.0.

## Start here

- [Installation](install.html) — `pip install invoiceguard`, requirements, install from source
- [Quickstart](quickstart.html) — the 5-minute flow: init, Stripe test key, first client, first deposit link
- [Configuration](configuration.html) — config file, environment variables, secret resolution

## How it works

1. **Before work starts:** `project create` generates a contract with a late-fee clause; `invoice create --kind deposit` makes a Stripe payment link. The client acknowledges the contract by accepting the deposit link.
2. **After the due date:** `check-due` (run from cron) sends the next escalation stage using editable markdown templates. Idempotent — a stage is never sent twice for the same invoice.
3. **When the client pays:** Stripe's webhook (`POST /webhooks/stripe`) marks the invoice paid automatically.

## Reference

| Page | What it covers |
|---|---|
| [CLI reference](cli.html) | every command and option |
| [Dashboard](dashboard.html) | the localhost web UI and its routes |
| [Dunning engine](dunning.html) | 3-stage escalation, templates, cron |
| [Webhooks](webhooks.html) | Stripe webhook setup and secret resolution |
| [Contracts](contracts.html) | contract generator and the acknowledgment model |
| [Database](database.html) | SQLite schema (the frozen CLI↔dashboard contract) |
| [Docker](docker.html) | compose setup |
| [Troubleshooting](troubleshooting.html) | common problems and fixes |
| [Changelog](changelog.html) | release history |
| [Roadmap](roadmap.html) | what's planned, honestly labeled |

## Honest scope

v1's "signature" is a client-acknowledgment checkbox (the deposit link being paid), not a legal e-signature. Stripe live calls were verified with a mocked SDK in this build environment; you add your own test key. Email deliverability is your SMTP. See [Troubleshooting](troubleshooting.html) and [Limits](troubleshooting.html#limits-of-v1) for the full picture.
