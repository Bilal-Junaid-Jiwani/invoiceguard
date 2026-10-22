---
title: Roadmap
eyebrow: Reference
description: What's planned for InvoiceGuard — honestly labeled as plans, not shipped features.
---
These are **plans, not promises** — nothing below is shipped in v0.9.0.

- ~~**Real e-signature**~~ — shipped in v0.3.0: typed-name / drawn-signature capture on the contract, stored with the project, SHA-256 tamper evidence.
- ~~**Late-fee accrual calculator**~~ — shipped in v0.4.0: monthly-compounding accrual of contract §3 fees, `invoice late-fees` CLI, dashboard rows, dunning template variables.
- ~~**SMS / WhatsApp escalation**~~ — shipped in v0.5.0: day-15 also goes out as an SMS/WhatsApp message via Twilio when configured, plus `invoice notify` for one-off reminders.
- **Agency mode** — multi-freelancer workspaces (unshipped); per-client dunning policies shipped in v0.7.0 (`invoiceguard client set-dunning-policy`); freelancer roster + invoice attribution shipped in v0.8.0 (`invoiceguard freelancer add`, `invoiceguard invoice assign`); freelancer earnings shipped in v0.9.0 (`invoiceguard freelancer show`, per-currency billed/collected/outstanding).
- ~~**Partial payments / payment plans**~~ — shipped in v0.2.0.

Priorities follow real user feedback. If a roadmap item matters to you, open an issue on [GitHub](https://github.com/Bilal-Junaid-Jiwani/invoiceguard) describing your workflow.
