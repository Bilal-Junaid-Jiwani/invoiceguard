---
title: Database
eyebrow: User guide
description: The InvoiceGuard SQLite schema — the frozen contract shared between the CLI and the dashboard.
---
SQLite, default `~/.invoiceguard/invoiceguard.db` (override with `$INVOICEGUARD_DB`). Both the CLI and the dashboard read and write these tables — **column names and types are frozen; additive migrations only.**

```sql
CREATE TABLE clients(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE projects(
  id INTEGER PRIMARY KEY,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  title TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  deposit_pct REAL NOT NULL DEFAULT 50.0,
  late_fee_pct REAL NOT NULL DEFAULT 1.5,
  late_fee_grace_days INTEGER NOT NULL DEFAULT 15,
  contract_md TEXT NOT NULL,
  contract_ack INTEGER NOT NULL DEFAULT 0,
  contract_ack_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE invoices(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  kind TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'draft',
  stripe_url TEXT,
  stripe_session_id TEXT,
  due_date TEXT,
  sent_at TEXT,
  paid_at TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE dunning_events(
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  stage TEXT NOT NULL,
  sent_at TEXT NOT NULL
);
```

## Enumerations

- Invoice **kinds**: `deposit | milestone | final`
- Invoice **statuses**: `draft | sent | paid | overdue | void`
- Dunning **stages**: `day1 | day7 | day15`

## Notes

- Money is stored as integer **cents** (`amount_cents`) — no float rounding bugs.
- Timestamps are ISO-8601 UTC strings.
- The dashboard's `web/db.py` module is the second implementation of this schema used by the web app; both follow the same frozen contract.
- `seed_demo.py` (in the repo) seeds a demo database where every sample name carries a `(demo)` suffix so demo data can never be mistaken for real clients.
