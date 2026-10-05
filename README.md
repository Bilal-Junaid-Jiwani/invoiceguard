# InvoiceGuard

<p align="center">
  <img src="https://raw.githubusercontent.com/Bilal-Junaid-Jiwani/invoiceguard/main/docs/assets/img/logo.svg" width="96" height="96" alt="InvoiceGuard logo — a shield with an invoice and checkmark">
</p>

[![PyPI](https://img.shields.io/pypi/v/invoiceguard.svg)](https://pypi.org/project/invoiceguard/)
[![Python](https://img.shields.io/pypi/pyversions/invoiceguard.svg)](https://pypi.org/project/invoiceguard/)
[![License](https://img.shields.io/github/license/Bilal-Junaid-Jiwani/invoiceguard.svg)](https://github.com/Bilal-Junaid-Jiwani/invoiceguard/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-website-12805C)](https://bilal-junaid-jiwani.github.io/invoiceguard/)
[![Website](https://img.shields.io/badge/website-live-A8802F)](https://bilal-junaid-jiwani.github.io/invoiceguard/site/)

**Freelancer payment enforcement: deposit links tied to a contract with a late-fee clause — then automated, escalating collections.**

Solo freelancers don't get paid. A Kaplan Group report (April 2026) found **85% of freelancers experience late payment**; a Freelancers Union survey found **91%** have experienced late/overdue payments, with 54% waiting 3+ months. InvoiceGuard's wedge:

1. **Before work starts:** generate a contract with a late-fee clause, create a Stripe deposit payment link. The client acknowledges the contract by accepting (paying) the deposit link.
2. **After the due date:** an automated 3-stage email escalation — day-1 polite nudge → day-7 firm reminder → day-15 formal notice quoting the late-fee clause — until a Stripe webhook marks the invoice paid.

Local-first (SQLite), open-source (Apache-2.0), works alongside your existing invoicing tools via Stripe. No platform switch required.

> **Honest scope:** contracts are signed electronically in the browser (typed name + drawn signature, SHA-256 hash of the signed text stored as tamper evidence) — a captured signature, not a qualified third-party e-signature service. See [Limits of v1](#limits-of-v1).

![InvoiceGuard dashboard — invoice list with escalation stages](https://raw.githubusercontent.com/Bilal-Junaid-Jiwani/invoiceguard/main/docs/assets/img/dashboard-list.png)

---

## 5-minute quickstart

**0. Install**

```bash
pip install invoiceguard
# or, from a local clone:  pip install -r requirements.txt && pip install -e .
invoiceguard init               # creates ~/.invoiceguard (config template, DB, email templates)
```

**1. Add your Stripe test key** (~2 min)

1. Go to <https://dashboard.stripe.com/test/apikeys> (toggle **Test mode** on).
2. **Developers → API keys → Create secret key**, copy the `sk_test_...` value.
3. Paste it into `~/.invoiceguard/config.yaml` as `stripe_secret_key`, **or** export it:
   ```bash
   export INVOICEGUARD_STRIPE_SECRET_KEY="sk_test_..."
   ```

**2. Add your SMTP details** (~2 min) — how dunning emails are sent. In `~/.invoiceguard/config.yaml`:

```yaml
smtp:
  host: "smtp.gmail.com"
  port: 587
  username: "you@example.com"
  password: "YOUR_GMAIL_APP_PASSWORD"   # or export INVOICEGUARD_SMTP_PASSWORD
  from_addr: "you@example.com"
  use_tls: true
```

(Gmail: create an **App Password** at Google Account → Security → 2-Step Verification → App passwords. Any SMTP provider works.)

**3. Create a client, project, and deposit link** (~1 min)

```bash
invoiceguard client add --name "Acme Corp" --email "billing@acme.com"

invoiceguard project create --client "Acme Corp" --title "Website redesign" \
  --amount 2000 --deposit-pct 50 --late-fee-pct 1.5 --ack
# ^ generates the contract markdown (with the late-fee clause) and records
#   client acknowledgment (--ack = client accepted the deposit link)

invoiceguard invoice create --project 1 --kind deposit
# => invoice #1 (deposit): $1,000.00
#    pay link: https://buy.stripe.com/...
#    due: 2026-10-07   status: sent
```

Send the pay link to the client. That's the whole pre-work flow.

**4. Let collections run themselves**

```bash
# add to your crontab (runs the dunning scan every morning at 9:00):
0 9 * * * /path/to/venv/bin/invoiceguard check-due >> ~/.invoiceguard/check-due.log 2>&1
```

`invoiceguard check-due` scans invoices past their due date and sends the next escalation stage (day1 → day7 → day15), using the editable markdown templates in `~/.invoiceguard/templates/`. It's idempotent — a stage is never sent twice for the same invoice. When the client pays, Stripe's webhook flips the invoice to `paid` automatically.

**5. Stripe webhook (so payments mark invoices paid)**

1. In the Stripe dashboard: **Developers → Webhooks → Add endpoint**.
2. URL: `https://YOUR-HOST/webhooks/stripe` (the dashboard app serves this; see below).
3. Select event: `checkout.session.completed`.
4. Copy the **Signing secret** (`whsec_...`) into `stripe_webhook_secret` in the config (or export `INVOICEGUARD_STRIPE_WEBHOOK_SECRET`). The dashboard webhook reads the secret from the env var first, then the config file, then the legacy `STRIPE_WEBHOOK_SECRET` env var.

> ⭐ If InvoiceGuard helped you, a star means a lot — it helps other developers find the project.

---

## CLI reference

| Command | What it does |
|---|---|
| `invoiceguard init` | Create `~/.invoiceguard/` (config template, DB, email templates) |
| `invoiceguard client add --name N --email E` | Add a client |
| `invoiceguard client list` | List clients |
| `invoiceguard project create --client N --title T --amount 2000 [--deposit-pct 50] [--late-fee-pct 1.5] [--late-fee-grace-days 15] [--ack]` | Create project + generate contract markdown |
| `invoiceguard project ack ID` | Record client acknowledgment of the contract (deposit-link path) |
| `invoiceguard project sign-request ID` | Create a one-time e-signature link for the contract (`/sign/<token>`) |
| `invoiceguard project sign-status ID` | Show pending/signed state, signer, timestamp, contract hash |
| `invoiceguard project list` | List projects |
| `invoiceguard invoice create --project ID --kind deposit\|milestone\|final [--amount X] [--due-days 7]` | Create a Stripe payment link (status → `sent`) |
| `invoiceguard invoice list` | List invoices |
| `invoiceguard invoice record-payment ID --amount 250 [--note "check #1"] [--method bank]` | Record a (partial) payment; clearing the balance marks the invoice `paid`, otherwise it becomes `partially-paid` |
| `invoiceguard invoice mark-paid ID` | Manually mark paid (records the full outstanding balance in the payment ledger) |
| `invoiceguard invoice void ID` | Void an invoice |
| `invoiceguard check-due` | Run the dunning scan (cron target) |
| `invoiceguard dashboard [--port 8000]` | Launch the local web dashboard (`http://127.0.0.1:8000`) — invoice list, detail with escalation timeline, Stripe webhook receiver |

Kind defaults: `deposit` = `deposit_pct`% of project total; `final` = remainder after deposit; `milestone` = full amount unless `--amount` overrides.

## Configuration

`~/.invoiceguard/config.yaml` (override path with `$INVOICEGUARD_CONFIG`; DB with `$INVOICEGUARD_DB`):

```yaml
stripe_secret_key: "sk_test_..."        # or INVOICEGUARD_STRIPE_SECRET_KEY
stripe_webhook_secret: "whsec_..."       # or INVOICEGUARD_STRIPE_WEBHOOK_SECRET
smtp:
  host: "smtp.gmail.com"
  port: 587
  username: "you@example.com"
  password: "APP_PASSWORD"              # or INVOICEGUARD_SMTP_PASSWORD
  from_addr: "you@example.com"
  use_tls: true
app:
  base_url: "http://localhost:8000"
```

Keys come **only** from config/env — never hardcoded, never committed (`.gitignore` covers `config.yaml`, `.env`, `*.key`, and the local DB).

## Dunning templates

`~/.invoiceguard/templates/day1.md`, `day7.md`, `day15.md` — plain markdown with `{variables}`:

`{client_name}` `{project_title}` `{amount}` `{paid}` `{outstanding}` `{due_date}` `{days_overdue}` `{late_fee_pct}` `{pay_url}`

`{amount}` is the original invoice amount; `{paid}` / `{outstanding}` track the payment ledger so a client who already paid part of the bill sees their remaining balance. Edit them freely; `check-due` picks them up on the next run. Stage timing: day1 fires at ≥1 day overdue, day7 at ≥7, day15 at ≥15 — earliest unsent due stage wins, one email per invoice per run. Invoices stay in dunning until the outstanding balance is zero (`sent`/`overdue`/`partially-paid` with `due_date` in the past).

## Database schema (contract with the dashboard app)

SQLite, default `~/.invoiceguard/invoiceguard.db`. The dashboard app reads/writes these exact tables — column names and types are frozen; additive migrations only:

```sql
CREATE TABLE clients(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, created_at TEXT NOT NULL);
CREATE TABLE projects(id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id), title TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', deposit_pct REAL NOT NULL DEFAULT 50.0, late_fee_pct REAL NOT NULL DEFAULT 1.5, late_fee_grace_days INTEGER NOT NULL DEFAULT 15, contract_md TEXT NOT NULL, contract_ack INTEGER NOT NULL DEFAULT 0, contract_ack_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE invoices(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', status TEXT NOT NULL DEFAULT 'draft', stripe_url TEXT, stripe_session_id TEXT, due_date TEXT, sent_at TEXT, paid_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE dunning_events(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), stage TEXT NOT NULL, sent_at TEXT NOT NULL);
CREATE TABLE payments(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), amount_cents INTEGER NOT NULL CHECK (amount_cents > 0), method TEXT NOT NULL DEFAULT 'manual', note TEXT, paid_at TEXT NOT NULL);
CREATE TABLE signatures(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), token TEXT NOT NULL UNIQUE, status TEXT NOT NULL DEFAULT 'pending', signer_name TEXT, signature_image TEXT, contract_hash TEXT, signed_at TEXT, created_at TEXT NOT NULL);
```

kinds: `deposit | milestone | final`. statuses: `draft | sent | partially-paid | paid | overdue | void`. stages: `day1 | day7 | day15`.

The `payments` ledger (added in v0.2.0) records every payment — `record-payment`, `mark-paid`, and Stripe webhooks all write rows here, so outstanding balances are always derivable. Databases created before v0.2.0 get an automatic, labeled backfill row for invoices already marked paid.

## Web dashboard

`invoiceguard dashboard` launches the web dashboard for real — uvicorn serving
`invoiceguard.web.main:app` (default `http://127.0.0.1:8000`, `--port` to
change it). It shares this SQLite database: invoice list with status filters
(including `partially-paid`) and escalation stages, per-invoice detail with
paid/outstanding amounts, a payments ledger, a chronological timeline, and
the `POST /webhooks/stripe` endpoint that records the payment when Stripe
reports a completed checkout session (a session for less than the balance
leaves the invoice `partially-paid`).

Quick webhook smoke test (no network — the payload is signed locally):

```bash
INVOICEGUARD_DB=/tmp/ig-demo.db .venv/bin/python seed_demo.py --fresh
INVOICEGUARD_DB=/tmp/ig-demo.db INVOICEGUARD_STRIPE_WEBHOOK_SECRET=whsec_test_x \
  .venv/bin/invoiceguard dashboard
# -> http://127.0.0.1:8000/  (Ctrl-C to stop)
```

## Docker

```bash
docker compose up -d                 # CLI container, data in the invoiceguard-data volume
docker compose run --rm app check-due # one-off dunning scan
```

Put secrets in a `.env` file next to `docker-compose.yml` (never committed): `INVOICEGUARD_STRIPE_SECRET_KEY`, `INVOICEGUARD_STRIPE_WEBHOOK_SECRET`, `INVOICEGUARD_SMTP_PASSWORD`. Port 8000 is exposed for the dashboard.

## Running tests

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e .
.venv/bin/python -m pytest tests/ -q
```

89 tests, all green (2026-10-05): contract clause rendering (incl. the e-signature sign-off wording), dunning stage selection + idempotency, template rendering, webhook signature verification (real HMAC check, offline), Stripe link creation (mocked SDK, params asserted), CLI flows (incl. `project sign-request`/`sign-status`), e-signature DB ops (idempotent request, single-use token, tamper-evidence hash, ack flip) + full web signing flow (page render, validation, signed receipt, single-use enforcement), dashboard integration (page rendering + offline webhook checks incl. the `invoiceguard_invoice_id` metadata-key regression and the webhook-secret resolution regression), and a full end-to-end (init → client → project → invoice → `check-due` against a real local SMTP server → assert email captured + `dunning_events` row written).

## Limits of v1

- **No live Stripe call was verified in this build environment** — Stripe's connector isn't connected here. `invoice create` was verified with a mocked `stripe` SDK asserting the exact `PaymentLink.create` params (line items, price data, metadata); webhook handling was verified with **real** `stripe.Webhook.construct_event` signature verification using a test secret (works offline). You add your own test key (2 min, above) and the live path is standard Stripe API.
- **E-signature is browser-captured, not qualified.** `project sign-request` gives the client a one-time signing link: typed name + drawn signature on the contract, with the exact signed text hashed (SHA-256) and stored as tamper evidence. It is NOT a qualified third-party e-signature service (DocuSign/HelloSign); legal weight varies by jurisdiction. The older acknowledgment path still exists: paying the deposit link, or `project ack`.
- **Email deliverability is yours.** InvoiceGuard sends via *your* SMTP. Use a reputable provider (Gmail App Password, SendGrid, etc.) and warm up new addresses; check spam folders in testing.
- **Late-fee enforceability varies by jurisdiction.** The clause is a contractual starting point, not legal advice. Adjust `late_fee_pct` / grace days per project.

## Roadmap

- ~~Real e-signature~~ — shipped in v0.3.0: `project sign-request` / `project sign-status`, one-time `/sign/<token>` page with typed-name + drawn-signature capture and SHA-256 tamper evidence.
- **SMS / WhatsApp escalation** — day-15 via message, not just email.
- **Agency mode** — multi-freelancer workspaces, per-client dunning policies.
- Late-fee accrual calculator + ledger on the dashboard.
- ~~Partial payments / payment plans~~ — shipped in v0.2.0.

## Changelog

Release history lives in [CHANGELOG.md](CHANGELOG.md) — newest first, no fluff.

## License

Apache-2.0 — see [LICENSE](LICENSE).
