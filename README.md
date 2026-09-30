# InvoiceGuard

**Freelancer payment enforcement: deposit links tied to a contract with a late-fee clause — then automated, escalating collections.**

Solo freelancers don't get paid. A Kaplan Group report (April 2026) found **85% of freelancers experience late payment**; a Freelancers Union survey found **91%** have experienced late/overdue payments, with 54% waiting 3+ months. InvoiceGuard's wedge:

1. **Before work starts:** generate a contract with a late-fee clause, create a Stripe deposit payment link. The client acknowledges the contract by accepting (paying) the deposit link.
2. **After the due date:** an automated 3-stage email escalation — day-1 polite nudge → day-7 firm reminder → day-15 formal notice quoting the late-fee clause — until a Stripe webhook marks the invoice paid.

Local-first (SQLite), open-source (Apache-2.0), works alongside your existing invoicing tools via Stripe. No platform switch required.

> **Honest scope:** v1's "signature" is a client-acknowledgment checkbox (recorded when the deposit link is paid), not a legal e-signature. See [Limits of v1](#limits-of-v1).

---

## 5-minute quickstart

**0. Install** (InvoiceGuard isn't on PyPI yet — install straight from the repo)

```bash
pip install git+https://github.com/Bilal-Junaid-Jiwani/invoiceguard.git
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
4. Copy the **Signing secret** (`whsec_...`) into `stripe_webhook_secret` in the config (or `INVOICEGUARD_STRIPE_WEBHOOK_SECRET`).

---

## CLI reference

| Command | What it does |
|---|---|
| `invoiceguard init` | Create `~/.invoiceguard/` (config template, DB, email templates) |
| `invoiceguard client add --name N --email E` | Add a client |
| `invoiceguard client list` | List clients |
| `invoiceguard project create --client N --title T --amount 2000 [--deposit-pct 50] [--late-fee-pct 1.5] [--late-fee-grace-days 15] [--ack]` | Create project + generate contract markdown |
| `invoiceguard project ack ID` | Record client acknowledgment of the contract |
| `invoiceguard project list` | List projects |
| `invoiceguard invoice create --project ID --kind deposit\|milestone\|final [--amount X] [--due-days 7]` | Create a Stripe payment link (status → `sent`) |
| `invoiceguard invoice list` | List invoices |
| `invoiceguard invoice mark-paid ID` | Manually mark paid |
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

`{client_name}` `{project_title}` `{amount}` `{due_date}` `{days_overdue}` `{late_fee_pct}` `{pay_url}`

Edit them freely; `check-due` picks them up on the next run. Stage timing: day1 fires at ≥1 day overdue, day7 at ≥7, day15 at ≥15 — earliest unsent due stage wins, one email per invoice per run.

## Database schema (contract with the dashboard app)

SQLite, default `~/.invoiceguard/invoiceguard.db`. The dashboard app reads/writes these exact tables — column names and types are frozen; additive migrations only:

```sql
CREATE TABLE clients(id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT, created_at TEXT NOT NULL);
CREATE TABLE projects(id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id), title TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', deposit_pct REAL NOT NULL DEFAULT 50.0, late_fee_pct REAL NOT NULL DEFAULT 1.5, late_fee_grace_days INTEGER NOT NULL DEFAULT 15, contract_md TEXT NOT NULL, contract_ack INTEGER NOT NULL DEFAULT 0, contract_ack_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE invoices(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), kind TEXT NOT NULL, amount_cents INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', status TEXT NOT NULL DEFAULT 'draft', stripe_url TEXT, stripe_session_id TEXT, due_date TEXT, sent_at TEXT, paid_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE dunning_events(id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES invoices(id), stage TEXT NOT NULL, sent_at TEXT NOT NULL);
```

kinds: `deposit | milestone | final`. statuses: `draft | sent | paid | overdue | void`. stages: `day1 | day7 | day15`.

## Web dashboard

`invoiceguard dashboard` launches the web dashboard for real — uvicorn serving
`invoiceguard.web.main:app` (default `http://127.0.0.1:8000`, `--port` to
change it). It shares this SQLite database: invoice list with status filters
and escalation stages, per-invoice detail with a chronological timeline, and
the `POST /webhooks/stripe` endpoint that marks invoices paid when Stripe
reports a completed checkout session.

Quick webhook smoke test (no network — the payload is signed locally):

```bash
INVOICEGUARD_DB=/tmp/ig-demo.db .venv/bin/python seed_demo.py --fresh
INVOICEGUARD_DB=/tmp/ig-demo.db STRIPE_WEBHOOK_SECRET=whsec_test_x \
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

50 tests, all green (2026-09-30): contract clause rendering, dunning stage selection + idempotency, template rendering, webhook signature verification (real HMAC check, offline), Stripe link creation (mocked SDK, params asserted), CLI flows, dashboard integration (page rendering + offline webhook checks incl. the `invoiceguard_invoice_id` metadata-key regression), and a full end-to-end (init → client → project → invoice → `check-due` against a real local SMTP server → assert email captured + `dunning_events` row written).

## Limits of v1

- **No live Stripe call was verified in this build environment** — Stripe's connector isn't connected here. `invoice create` was verified with a mocked `stripe` SDK asserting the exact `PaymentLink.create` params (line items, price data, metadata); webhook handling was verified with **real** `stripe.Webhook.construct_event` signature verification using a test secret (works offline). You add your own test key (2 min, above) and the live path is standard Stripe API.
- **v1 acknowledgment is a checkbox, not a signature.** Paying the deposit link = the client accepted the terms; `contract_ack=1` records it. Real e-signature is roadmap.
- **Email deliverability is yours.** InvoiceGuard sends via *your* SMTP. Use a reputable provider (Gmail App Password, SendGrid, etc.) and warm up new addresses; check spam folders in testing.
- **Late-fee enforceability varies by jurisdiction.** The clause is a contractual starting point, not legal advice. Adjust `late_fee_pct` / grace days per project.

## Roadmap

- **Real e-signature** — typed-name / drawn-signature capture on the contract, stored with the project.
- **SMS / WhatsApp escalation** — day-15 via message, not just email.
- **Agency mode** — multi-freelancer workspaces, per-client dunning policies.
- Late-fee accrual calculator + ledger on the dashboard.
- Partial payments / payment plans.

## License

Apache-2.0 — see [LICENSE](LICENSE).
