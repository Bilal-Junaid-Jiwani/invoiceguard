# Changelog

Every release, newest first. InvoiceGuard follows semver.

## [0.44.0] - 2026-11-27

Agency-mode slice #38: CSV export for `invoice late-fees`. Slices #32–#37 put CSV exports on every agency view and every CLI list view, but the late-fee accrual view itself — the one invoice's due date, rate, grace window, months billed, accrued fees, and total due — was still text-only. This slice adds `--csv PATH` (or `-` for stdout) to `invoice late-fees`, writing the same accrual as a spreadsheet through one shared library call. With this, every CLI view in the app that prints numbers is now a spreadsheet.

### Added
- **`invoiceguard invoice late-fees ID [--as-of DATE] [--csv PATH]`** — one CSV row for the invoice, with the same columns the text view prints (invoice id, client, project, currency, due date, monthly rate, grace days, accrual start, as-of date, outstanding, months billed, accrued fees, total due). The amounts are the stored cents as plain decimal strings, like the other CSV exports; the text view renders them as money. The outstanding cell comes from the summary (`total_cents - fees_cents`), so the bare library call needs no ledger lookup. An invoice with no due date carries empty due-date and accrual-start cells and zero months/fees, exactly like the text view's "no due date: no fees accrue" line; an invoice whose accrual has started but no full month has elapsed yet still carries its accrual start, exactly like the text view. `-` prints the CSV to stdout instead of the text view; a file path writes the file and still prints the text view with a `wrote invoice late-fees CSV` note. `reports.late_fees_csv(invoice, summary, as_of=None)` is the library call — it takes the same `db.invoice_full()` dict and `late_fee_summary()` dict the text view prints, projected with `--as-of` (default: today). Pinned by a new test file `tests/test_invoice_late_fees_csv.py` (header + money columns, accrual not started, accrual started with no full month, no due date, partial payment accruing on the outstanding balance, default as-of is today, file + stdout modes, unknown-invoice error, invalid `--as-of` clean error, bare library call, CSV formula-injection neutralization). Docs: README late-fees row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.44.0)
- A view, not a workflow: the export reports one invoice's accrual as of `--as-of`, not a settlement. Recording a payment is still `invoice record-payment` / `invoice mark-paid`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.43.0] - 2026-11-26

Agency-mode slice #37: CSV export for `client list` and `project list`. Slices #32–#36 put CSV exports on every agency view (freelancer list/show, payout list/suggest/schedule/due/overdue, agency summary, invoice report, invoice list), but the two core rosters themselves — the client list with its per-client dunning-policy overrides and the project list with its contract-acknowledgment state — were still text-only. This slice adds `--csv PATH` (or `-` for stdout) to both, writing the same lists as spreadsheets through two shared library calls. With these two, every CLI list view in the app is now a spreadsheet.

### Added
- **`invoiceguard client list [--csv PATH]`** — one CSV row per client, in the same id order the text lines print them, with the same columns the text lines print (client id, name, email, phone — empty when unset, exactly like the text lines printing nothing), plus the per-client dunning-policy overrides from the text lines' `[dunning ...]` marker as one cell per stage (`dunning_day1` / `dunning_day7` / `dunning_day15`): the custom threshold, `off` when the stage is disabled for this client, empty when the client uses the global default. `reports.client_list_csv(rows)` is the library call — it takes the same `db.list_clients()` rows the text view prints and needs no timing map. Pinned by a new test file `tests/test_client_project_list_csv.py` (header + columns, multi-client rows in id order, dunning overrides incl. custom / disabled / default, no-clients header-only export, file + stdout modes, bare library call, CSV formula-injection neutralization). Docs: README client list row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.
- **`invoiceguard project list [--csv PATH]`** — one CSV row per project, in the same id order the text lines print them, with the same columns the text lines print (project id, title, client, amount, currency, contract acknowledgment — `ack` / `no-ack`, exactly like the bracket the text lines print). The amount is the stored cents as a plain decimal string, like the other CSV exports; the text view renders it as money. `reports.project_list_csv(rows)` is the library call — it takes the same `db.list_projects()` rows the text view prints and needs no timing map. Pinned by the same new test file (header + money columns, multi-project rows in id order, ack / no-ack states, no-projects header-only export, file + stdout modes, bare library call, CSV formula-injection neutralization). Docs: README project list row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.43.0)
- A view, not a workflow: the exports list clients and projects as they stand, not a settlement or a report. Receivables aging, late fees, and per-freelancer payout timing stay on `invoice report` (`--as-of` projection) and the other agency views. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.42.0] - 2026-11-25

Agency-mode slice #36: CSV export for `invoice list`. Slices #32–#35 put CSV exports on every agency view (freelancer list/show, payout list/suggest/schedule/due/overdue, agency summary, invoice report), but the core `invoice list` view itself — the one list an agency operator opens first to see the whole book — was still text-only. This slice adds `--csv PATH` (or `-` for stdout) to `invoice list`, writing the same list as a spreadsheet through one shared library call. The list carries no `--as-of`, so — unlike the invoice report CSV — there are no payout timing columns here.

### Added
- **`invoiceguard invoice list [--freelancer ID] [--csv PATH]`** — one CSV row per listed invoice, in the same id order the text lines print them, with the same columns the text lines print (invoice id, kind, status, amount, currency, project, freelancer, due date — freelancer empty when unassigned and due date empty when unset, exactly like the text lines omitting the bracketed name and printing `-`). The amount is the stored cents as a plain decimal string, like the other CSV exports; the text view renders it as money. An empty list exports a header-only CSV. `-` prints the CSV to stdout instead of the text view; a file path writes the file and still prints the text view with a `wrote invoice list CSV` note. `--freelancer ID` composes with the export, restricting the rows. `reports.invoice_list_csv(rows)` is the library call — it takes the same `db.list_invoices()` rows the text view prints and needs no timing map. Pinned by a new test file `tests/test_invoice_list_csv.py` (header + money columns, multi-invoice rows in id order, freelancer filter, no-invoices header-only export, file + stdout modes, unknown-freelancer error, bare library call, CSV formula-injection neutralization). Docs: README invoice list row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.42.0)
- A view, not a workflow: the export lists invoices as they stand, not a settlement or a report. Receivables aging, late fees, and per-freelancer payout timing stay on `invoice report` (`--as-of` projection) and the other agency views. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.41.1] - 2026-11-24

Agency-mode slice #35: CSV export for the recorded payouts of `freelancer show`. Slices #33–#34 put the earnings detail and the attributed invoice lines of `freelancer show` in spreadsheets, but the recorded payouts underneath them — the per-payout amount, date, method, and note that the earnings lines only summarize as paid-out totals — still had to be retyped from the text view. This slice adds `--payouts-csv PATH` (or `-` for stdout) to `freelancer show`, writing the same recorded payouts as a spreadsheet through one shared library call. With all three exports the whole `freelancer show` screen is now a spreadsheet.

### Added
- **`invoiceguard freelancer show ID [--as-of DATE] [--csv PATH] [--invoices-csv PATH] [--payouts-csv PATH]`** — one CSV row per recorded payout, in the same newest-first order the text lines print them, with the same columns the text lines print (payout id, currency, amount, paid date, method, note — method and note empty when unset, exactly like the text lines omitting them) — with the freelancer id and name on every row so the file stands alone — plus the same per-currency payout timing in trailing `payout_status` / `payout_date` columns — the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule — taken from the same `agency_summary()` rows keyed by currency the earnings lines use, projected with `--as-of` (default: today). The date cell carries the full stored `paid_at` value, like the `payout list` CSV export; the text view truncates it to the date for display. A freelancer with no recorded payouts exports a header-only CSV. `-` prints the CSV to stdout instead of the text view; a file path writes the file and still prints the text view with a `wrote freelancer show payouts CSV` note. `--csv`, `--invoices-csv`, and `--payouts-csv` can be used together to separate files; only one may print to stdout. `reports.freelancer_show_payouts_csv(freelancer, payouts, timing=None)` is the library call — without a timing map both columns stay empty. Pinned by a new test file `tests/test_freelancer_show_payouts_csv.py` (header + money columns, multi-payout rows newest first, per-currency timing, `--as-of` projection, no-payouts header-only export, file + stdout modes, all three CSVs to files, two to stdout is a clean error, invalid `--as-of` clean error, unknown-freelancer error, bare library call with and without timing, CSV formula-injection neutralization). Docs: README freelancer show row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.41.1)
- A view, not a workflow: the export reports the freelancer's recorded payouts as of `--as-of`, not a settlement. Recording a payout is still `payout settle` / `payout record`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.41.0] - 2026-11-23

Security release — the fixes from a full security audit of the web app, both Stripe webhook paths, and the e-signature flow (2 High, 1 Medium, 2 plain bugs; every finding reproduced before it was fixed). No new user-facing features; everyone who serves the dashboard app or receives Stripe webhooks should upgrade.

### Fixed
- **Stripe webhook replays could over-record payments and close an invoice on partial money (High).** The served `POST /webhooks/stripe` endpoint never deduped Stripe event ids, and `mark_invoice_paid` never deduped checkout sessions — so when Stripe redelivered a partial-payment event (its normal at-least-once retry), each delivery booked the payment again until the invoice flipped to `paid` (reproduced: one $40 session delivered 3× recorded $100 and closed a $100 invoice). The endpoint now records processed event ids in a `webhook_events` table (the same table the standalone handler already used) and acknowledges redeliveries without re-applying them; `mark_invoice_paid` additionally refuses to record a second payment for a session id already in the ledger, which also closes the two-distinct-events-one-session variant (`checkout.session.completed` followed by `checkout.session.async_payment_succeeded`), and its read-check-write now runs under `BEGIN IMMEDIATE` so concurrent deliveries cannot race the dedupe check.
- **The dashboard pages had no authentication (High).** `GET /` and `GET /invoices/{id}` rendered every client's name/email, amounts, and payment history to anyone who could reach the app — and the documented webhook/signing deployment (public host, Docker port publish) puts those pages on the same surface as the routes that must be public. The data pages now require a dashboard token when one is configured (see Added); the webhook (Stripe-signature verified), `/sign/{token}` (capability URL), and `/healthz` stay open, as they must.
- **Contract signing was a check-then-act race (Medium).** Both `sign_contract` implementations (web layer and core DB) checked `status = 'pending'` and then updated without re-testing it, so two concurrent submissions on one single-use link both succeeded and issued conflicting receipts, the stored signer being whoever committed last. The UPDATE now carries `AND status = 'pending'` and the loser is rejected with "already used" (rowcount-checked) — single-use is enforced atomically, pinned by deterministic concurrency tests that hold the check→act window open.
- **The standalone webhook handler crashed on void-invoice and zero-amount events (bug).** `_apply_event` let `record_payment`'s `ValueError` escape for a payment completing on a voided invoice (a client paying an outstanding link after a void) and for a zero-amount session — and because the event was never marked processed, it became a poison event that crashed on every redelivery. Both cases are now ignored gracefully (`ignored:void` / `ignored:zero_amount`), mirroring the FastAPI path, and the event is marked processed.
- **`invoices.stripe_session_id` held two kinds of id (bug).** `invoice create` stored the Stripe Payment Link id (`plink_...`) in `stripe_session_id`, whose readers expect a checkout session id (`cs_...`), so the web webhook's session-id fallback lookup could never match a CLI-created invoice (masked in practice by the metadata hint). The link id now lives in its own column, `invoices.stripe_link_id` (additive migration, applied on connect), `stripe_session_id` holds session ids only, and the fallback lookup only ever matches `cs_` values — pre-0.41.0 databases carrying a `plink_` value in the column keep working through the metadata hint, exactly as before.

### Added
- **Dashboard token authentication.** Set `INVOICEGUARD_DASHBOARD_TOKEN` (or `dashboard_token` in `~/.invoiceguard/config.yaml`) and the dashboard pages require it: an `Authorization: Bearer <token>` header, or `?token=<token>` in the URL — a successful query-token visit sets an HttpOnly cookie so in-dashboard links keep working. `invoiceguard dashboard` reports the auth state at launch, and when NO token is configured the app logs and prints a loud startup warning that the dashboard is unauthenticated, so the insecure state is never silent (localhost-only use stays possible, now explicitly). Docs: README web-dashboard section, docs dashboard + configuration pages.
- 18 new tests (593 total): webhook replay ×3 end-to-end (partial payment recorded exactly once), distinct-events-one-session, direct `mark_invoice_paid` session redelivery, dashboard auth (401 without/with wrong token, 200 with Bearer, `?token=` + cookie flow, config-file token, webhook/sign/healthz unaffected, no-token warning), concurrent-signer races for both `sign_contract` implementations, standalone-handler void/zero-amount handling, and the `stripe_link_id` split (CLI storage + fallback matching). The new regression tests fail against the pre-fix code.

### Honest scope (0.41.0)
- Token auth is a single shared operator credential, not user accounts — it separates "the operator" from "anyone who can reach the app", which is the gap the audit found. If the app is exposed beyond a trusted host, still put it behind TLS (the token travels in headers/cookies) and prefer exposing only `/webhooks/*` and `/sign/*` through a proxy.
- Event-id dedupe covers redeliveries of the same event; session-level dedupe covers repeat application of one checkout session. A second, genuinely distinct checkout session for the same invoice still records its payment, as it should.

## [0.40.0] - 2026-11-23

Agency-mode slice #34: CSV export for the attributed invoice lines of `freelancer show`. Slice #33 put the earnings detail in a spreadsheet, but the invoice lines underneath it — the per-invoice amount, collected, and outstanding that the earnings lines only summarize — still had to be retyped from the text view. This slice adds `--invoices-csv PATH` (or `-` for stdout) to `freelancer show`, writing the same attributed invoice lines as a spreadsheet through one shared library call.

### Added
- **`invoiceguard freelancer show ID [--as-of DATE] [--csv PATH] [--invoices-csv PATH]`** — one CSV row per attributed invoice, in the same id order the text view prints them, with the same columns the text lines print (invoice id, client, currency, amount, collected, outstanding, status — with the freelancer id and name on every row so the file stands alone), plus the same timing in trailing `payout_status` / `payout_date` columns — the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule — taken from the same `agency_summary()` rows keyed by currency the earnings lines use, projected with `--as-of` (default: today). Drafts and voids stay excluded, exactly like the text view; a freelancer with no attributed invoices exports a header-only CSV. `-` prints the CSV to stdout instead of the text view; a file path writes the file and still prints the text view with a `wrote freelancer show invoices CSV` note. `--csv` and `--invoices-csv` can be used together to separate files; only one may print to stdout. `reports.freelancer_show_invoices_csv(freelancer, invoices, timing=None)` is the library call — without a timing map both columns stay empty. Pinned by a new test file `tests/test_freelancer_show_invoices_csv.py` (header + money columns, multi-invoice rows in id order, drafts/voids excluded, per-currency timing, `--as-of` projection, no-attributed-invoices header-only export, file + stdout modes, both CSVs to files, both to stdout is a clean error, invalid `--as-of` clean error, unknown-freelancer error, bare library call with and without timing, CSV formula-injection neutralization). Docs: README freelancer show row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.40.0)
- A view, not a workflow: the export reports the freelancer's attributed invoices as of `--as-of`, not a settlement. Recording a payout is still `payout settle` / `payout record`. The recorded payouts stay text-only — this exports the earnings detail and the invoice lines, not the whole screen. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.39.0] - 2026-11-22

Agency-mode slice #33: CSV export for `freelancer show`. Slices #14–#32 put payout timing on every CLI view, on both dashboard pages, and on the invoice report, agency summary, payout ledger, pay-run, suggestion, schedule, and roster CSV exports — but drilling into one freelancer still had to be retyped from the text view: the per-freelancer earnings detail, with its split-derived expected share and still-due-to-freelancer columns, appeared in no spreadsheet at all. This slice adds `--csv PATH` (or `-` for stdout) to `freelancer show`, writing the same earnings detail as a spreadsheet through one shared library call.

### Added
- **`invoiceguard freelancer show ID [--as-of DATE] [--csv PATH]`** — one CSV row per currency earnings line, in the same order the text view prints them, with the same columns the text lines print (freelancer id, freelancer, email, phone, split percentage when set, schedule cadence and day, currency, billed, collected, outstanding, invoice count, paid out, still owed, expected share of collected, still due to the freelancer after payouts), plus the same payout timing in trailing `payout_status` / `payout_date` columns — the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule — taken from the same `agency_summary()` rows keyed by currency the text lines use, projected with `--as-of` (default: today). No-split rows carry empty `split_pct`, `expected_share`, and `still_due_to_freelancer` cells rather than invented 0s, exactly like the text view omitting the split and the share; unscheduled rows carry empty schedule cells; a freelancer with no billable invoices gets one row with an empty currency, zero amounts, and empty share and timing cells rather than a misleading status. `-` prints the CSV to stdout instead of the text view; a file path writes the file and still prints the text view with a `wrote freelancer show CSV` note. `reports.freelancer_show_csv(freelancer, rows, timing=None)` is the library call — without a timing map both columns stay empty. Pinned by a new test file `tests/test_freelancer_show_csv.py` (header + money columns, contact and schedule columns, split and no-split rows, due / overdue / upcoming / settled / no-billable timing, multi-currency rows, `--as-of` projection, file + stdout modes, invalid `--as-of`, unknown-freelancer error, bare library call with and without timing, CSV formula-injection neutralization). Docs: README freelancer show row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.39.0)
- A view, not a workflow: the export reports the freelancer's earnings detail as of `--as-of`, not a settlement. Recording a payout is still `payout settle` / `payout record`. The attributed invoice lines and the recorded payouts stay text-only — this exports the earnings detail, not the whole screen. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.38.0] - 2026-11-21

Agency-mode slice #32: CSV export for `freelancer list`. Slices #14–#31 put payout timing on every CLI view, on both dashboard pages, and on the invoice report, agency summary, payout ledger, pay-run, suggestion, and schedule CSV exports — but the roster itself had no spreadsheet export at all: the agency's contact list still had to be retyped from the text lines. This slice adds `--csv PATH` (or `-` for stdout) to `freelancer list`, writing the same roster as a spreadsheet through one shared library call.

### Added
- **`invoiceguard freelancer list [--as-of DATE] [--csv PATH]`** — one CSV row per roster freelancer, in the same order the text list prints them, with the same columns the text lines print (freelancer id, freelancer, email, phone, split percentage when set), plus the same payout timing in trailing `payout_status` / `payout_date` columns — the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule — taken from the same `payout_schedule_timing()` map the text lines use, projected with `--as-of` (default: today). No-split rows carry an empty `split_pct` cell rather than an invented 0, exactly like the text list omitting the split; freelancers with no billable invoices carry empty timing cells rather than a misleading status, and a freelancer whose currencies disagree carries no status rather than a guessed one. `-` prints the CSV to stdout instead of the text list; a file path writes the file and still prints the text list with a `wrote freelancer list CSV` note. An empty roster exports a header-only CSV. `reports.freelancer_list_csv(rows, timing=None)` is the library call — without a timing map both columns stay empty. Pinned by a new test file `tests/test_freelancer_list_csv.py` (header + columns, contact columns, split and no-split rows, due / overdue / upcoming / settled / no-schedule / no-billable timing, `--as-of` projection, file + stdout modes, empty roster, invalid `--as-of`, bare library call with and without timing, mixed-status empty cells, CSV formula-injection neutralization). Docs: README freelancer list row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.38.0)
- A view, not a workflow: the export reports the roster as of `--as-of`, not a settlement. Recording a payout is still `payout settle` / `payout record`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.37.0] - 2026-11-20

Agency-mode slice #31: CSV export for `payout schedule`. Slices #14–#30 put payout timing on every CLI view, on both dashboard pages, and on the invoice report, agency summary, payout ledger, pay-run, and suggestion CSV exports — but the "who gets paid when" roster itself had no spreadsheet export at all: the schedule list still had to be retyped from the text lines. This slice adds `--csv PATH` (or `-` for stdout) to `payout schedule`, writing the same schedules as a spreadsheet through one shared library call. It also fixes a version drift: 0.36.0 shipped with `invoiceguard --version` still printing 0.35.0.

### Added
- **`invoiceguard payout schedule [--freelancer ID] [--as-of DATE] [--csv PATH]`** — one CSV row per roster freelancer, in the same order the text list prints them, with the same columns the text lines print (freelancer id, freelancer, schedule cadence and day, next payout date), plus the same payout timing in trailing `payout_status` / `payout_date` columns — the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule — taken from the same `payout_schedule_timing()` map the text lines use, projected with `--as-of` (default: today). Unscheduled rows carry empty schedule and date cells, exactly like the text list's "no schedule set" line; freelancers with no billable invoices carry empty timing cells rather than a misleading status, and a freelancer whose currencies disagree carries no status rather than a guessed one. `-` prints the CSV to stdout instead of the text list; a file path writes the file and still prints the text list with a `wrote payout schedule CSV` note. An empty roster exports a header-only CSV. `reports.payout_schedule_csv(rows, timing=None)` is the library call — without a timing map both columns stay empty. Pinned by a new test file `tests/test_payout_schedule_csv.py` (header + columns, scheduled and unscheduled rows, due / overdue / upcoming / settled / no-schedule / no-billable timing, `--as-of` projection, `--freelancer` filter, file + stdout modes, empty roster, invalid `--as-of`, unknown-freelancer error, bare library call with and without timing, mixed-status empty cells, CSV formula-injection neutralization). Docs: README payout schedule row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Fixed
- **`invoiceguard --version` printed the previous release's version** — 0.36.0 shipped with `__init__.__version__` still at 0.35.0 (the release commit bumped pyproject.toml but not the module), so the installed 0.36.0 reported itself as 0.35.0. The module version is now 0.37.0, matching pyproject.toml, and a new regression test (`test_version_matches_pyproject`) asserts the two agree and that `--version` prints the packaged version, so a future release commit cannot silently drift again.

### Honest scope (0.37.0)
- A view, not a workflow: the export reports the schedules as of `--as-of`, not a settlement. Recording a payout is still `payout settle` / `payout record`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.36.0] - 2026-11-19

Agency-mode slice #30: CSV export for `payout suggest`. Slices #14–#29 put payout timing on every CLI view, on both dashboard pages, and on the invoice report, agency summary, payout ledger, and pay-run CSV exports — but the "what do I pay next" list itself had no spreadsheet export at all: the suggestion list still had to be retyped from the text lines. This slice adds `--csv PATH` (or `-` for stdout) to `payout suggest`, writing the same suggestions as a spreadsheet through one shared library call.

### Added
- **`invoiceguard payout suggest [--freelancer ID] [--as-of DATE] [--csv PATH]`** — one CSV row per suggestion per currency, in the same order the text list prints them, with the same columns the text lines print (freelancer id, freelancer, currency, the still-due split share, the still-held balance, the collected and already-paid amounts the text lines show in parentheses, and the split the share was derived from), plus the same payout timing in trailing `payout_status` / `payout_date` columns — the missed run date when overdue, the next payout date when due or upcoming, empty for no schedule — taken from the same `payout_suggestion_timing()` map the text lines use, projected with `--as-of` (default: today). No-split rows carry an empty `suggested` cell and the still-held balance in `available`, exactly like the text list's "(available — no split set)" label; no-split rows also carry an empty `split_pct` cell rather than an invented split. A suggestion row is actionable by construction, so `settled` never appears in the timing columns. `-` prints the CSV to stdout instead of the text list; a file path writes the file and still prints the text list with a `wrote payout suggest CSV` note. An empty suggestion list exports a header-only CSV. `reports.payout_suggestions_csv(rows, timing=None)` is the library call — without a timing map both columns stay empty. Pinned by a new test file `tests/test_payout_suggest_csv.py` (header + money columns, split and no-split rows, due / overdue / upcoming / no-schedule timing, `--as-of` projection, `--freelancer` filter, file + stdout modes, empty suggestion list, empty roster, invalid `--as-of`, unknown-freelancer error, bare library call with and without timing, CSV formula-injection neutralization). Docs: README payout suggest row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.36.0)
- A view, not a workflow: the export reports the suggestions as of `--as-of`, not a settlement. Recording a suggestion is still `payout settle` / `payout record`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.35.0] - 2026-11-18

Agency-mode slice #29: CSV export for `payout due` and `payout overdue`. Slices #14–#28 put payout timing on every CLI view, on both dashboard pages, and on the invoice report, agency summary, and payout ledger CSV exports — but the pay run itself had no spreadsheet export at all: the "who do we pay this week, and who did we miss?" lists still had to be retyped from the text output. This slice adds `--csv PATH` (or `-` for stdout) to both `payout due` and `payout overdue`, writing the same pay run as a spreadsheet through one shared library call.

### Added
- **`invoiceguard payout due [--freelancer ID] [--as-of DATE] [--within-days N] [--csv PATH]`** — one CSV row per due freelancer per currency, in the same order the text list prints them, with the same columns the text lines print (freelancer id, freelancer, currency, the still-due split share, the still-held balance, the schedule's cadence and day, and the next payout date). No-split rows carry an empty `suggested` cell and the still-held balance in `available`, exactly like the text list's "(available — no split set)" label; due rows carry an empty `missed_payout_date` cell rather than an invented missed date. Projected with `--as-of` and `--within-days` like the text list. `-` prints the CSV to stdout instead of the text list; a file path writes the file and still prints the text list with a `wrote payout due CSV` note. An empty pay run exports a header-only CSV. `reports.pay_run_csv(rows)` is the library call. Pinned by a new test file `tests/test_payout_due_overdue_csv.py` (header + money columns, split and no-split rows, `--within-days`, `--as-of` projection, `--freelancer` filter, file + stdout modes, empty pay run, empty roster, invalid `--as-of`, unknown-freelancer error, bare library call, CSV formula-injection neutralization).
- **`invoiceguard payout overdue [--freelancer ID] [--as-of DATE] [--csv PATH]`** — the same export for the missed pay runs: one row per overdue freelancer per currency with the same columns, plus the `missed_payout_date` the text lines print. Due-today freelancers never appear here (they are in `payout due`), and freelancers paid at or after the missed run produce no row — the same `payout_overdue()` rules the text list uses, projected with `--as-of`. Same file/stdout modes and `wrote payout overdue CSV` note; an empty missed run exports a header-only CSV. Pinned by the same new test file (header + money columns, no-split rows, due-today exclusion, paid-at-missed-run exclusion, `--freelancer` filter, file mode, empty missed run, invalid `--as-of`).

### Honest scope (0.35.0)
- A view, not a workflow: the export reports the pay run as of `--as-of`, not a settlement. Recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.34.0] - 2026-11-17

Agency-mode slice #28: CSV export for `payout list`. Slices #14–#27 put payout timing on every CLI view, on both dashboard pages, and on the invoice report and agency summary CSV exports — but the payout ledger itself had no spreadsheet export at all: the accountant's record of what the agency already sent still had to be retyped from the text list. This slice adds `--csv PATH` (or `-` for stdout) to `payout list`, writing the same ledger as a spreadsheet.

### Added
- **`invoiceguard payout list [--freelancer ID] [--as-of DATE] [--csv PATH]`** — one CSV row per recorded payout, newest first, with the same columns the text list prints (payout id, freelancer, currency, amount, paid-at, method, note), plus the same per-freelancer payout timing in trailing `payout_status` / `payout_date` columns — on the newest row per freelancer only, exactly like the text list (the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule), taken from the same `payout_schedule_timing()` rollup — no new library rules, projected with `--as-of` (default: today). Older rows for the same freelancer carry empty timing cells; a freelancer with no timing entry carries empty cells rather than a misleading status. `-` prints the CSV to stdout instead of the text list; a file path writes the file and still prints the text list with a `wrote payout list CSV` note. An empty ledger exports a header-only CSV. `reports.payouts_csv(rows, timing=None)` is the library call — without a timing map both columns stay empty. Pinned by a new test file `tests/test_payout_list_csv.py` (header + money columns, due, overdue, upcoming, settled, no-schedule, timing only on newest row, per-freelancer timing, `--as-of` projection, `--freelancer` filter, file + stdout modes, empty ledger, invalid `--as-of`, bare library call with and without timing, CSV formula-injection neutralization). Docs: README payout list row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.34.0)
- A view, not a workflow: the export reports the freelancer's payout position as of `--as-of`, not a settlement. Recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.33.0] - 2026-11-16

Agency-mode slice #27: CSV export for `agency summary`. Slices #14–#26 put payout timing on every CLI view, on both dashboard pages, and on both `invoice report` CSV exports — but the whole-book view itself had no spreadsheet export at all: the accountant's whole-book sheet still had to be retyped from the text summary. This slice adds `--csv PATH` (or `-` for stdout) to `agency summary`, writing the same view as a spreadsheet.

### Added
- **`invoiceguard agency summary [--freelancer ID] [--as-of DATE] [--csv PATH]`** — one CSV row per freelancer per currency with the same money columns the text summary prints (billed, collected, outstanding, paid out, still owed, plus the suggested payout where one exists), plus one TOTALS row per currency carrying the same rollup timing the TOTALS lines print. Every row carries its payout timing in trailing `payout_status` / `payout_date` columns (the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule), taken straight from the rows' own timing fields — no new library rules, projected with `--as-of` (default: today). Freelancers with no billable invoices get one row with an empty currency, zero amounts, and empty timing cells rather than being dropped; a mixed settled / no-schedule currency carries empty timing on its TOTALS row rather than a misleading status. `-` prints the CSV to stdout instead of the text summary; a file path writes the file and still prints the text summary with a `wrote agency summary CSV` note. `reports.agency_summary_csv(rows, totals=None)` is the library call — without totals it returns just the freelancer rows, timing columns still populated. Pinned by a new test file `tests/test_agency_summary_csv.py` (header + money columns, due, overdue, upcoming, settled, no-schedule, per-currency, no-billable row, mixed-status TOTALS, `--as-of` projection, `--freelancer` filter, file + stdout modes, empty roster, invalid `--as-of`, bare library call, CSV formula-injection neutralization). Docs: README agency summary row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.33.0)
- A view, not a workflow: the export reports the freelancer's payout position as of `--as-of`, not a settlement. Recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.32.0] - 2026-11-15

Agency-mode slice #26: payout timing in the `invoice report` payments CSV export. Slices #14–#25 put timing on every CLI view, on both dashboard pages, and on the invoice CSV export — but the `--payments-csv` export dropped it: the payment ledger answered "what came in" with no hint of "who do we owe for this work, and when". This slice joins the same per-currency timing the text report's "by freelancer" lines carry onto each payment row, through the payment's invoice.

### Added
- **`invoiceguard invoice report [--as-of DATE] [--payments-csv PATH]`** — each payment CSV row now carries two trailing columns, `payout_status` (due / overdue / upcoming / settled / no schedule) and `payout_date` (the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule), taken from the same `agency_summary()` rows the text report joins onto its "by freelancer" lines — per currency, matching the payment's own currency, projected with `--as-of` (default: today). The join is through the payment's invoice: its attribution and its currency, so a payment in EUR carries the freelancer's EUR timing, not the USD one. Payments on unassigned invoices carry empty cells rather than a misleading status, and payments against invoices that are now paid in full still export — with timing. `payments_csv(db, timing=None)` takes the same per-(freelancer, currency) timing map; without it both columns stay empty, so a bare `payments_csv(db)` call gains no invented status. `db.all_payments()` now also returns the invoice's `freelancer_id` for the join. Pinned by a new test file `tests/test_invoice_report_payments_csv_timing.py` (due, overdue, upcoming, settled, no-schedule, per-currency, unassigned, `--as-of` projection, paid-invoice rows, bare-call empty columns). Docs: README invoice report row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.32.0)
- The timing describes the freelancer's payout position as of `--as-of`, not the payment itself: a payment row never says "this payment settled the payout". Recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.31.0] - 2026-11-14

Agency-mode slice #25: payout timing in the `invoice report` CSV export. Slices #14–#24 put timing on every CLI view and on both dashboard pages — but the `--csv` export dropped it: the accountant's spreadsheet answered "who owes us" with no hint of "who do we owe, and when". This slice joins the same per-currency timing the text report's "by freelancer" lines carry onto each CSV row.

### Added
- **`invoiceguard invoice report [--as-of DATE] [--csv PATH]`** — each invoice CSV row now carries two trailing columns, `payout_status` (due / overdue / upcoming / settled / no schedule) and `payout_date` (the missed run date when overdue, the next payout date when due or upcoming, empty for settled / no schedule), taken from the same `agency_summary()` rows the text report joins onto its "by freelancer" lines — per currency, matching the row's own currency, projected with `--as-of` (default: today). Unassigned invoices carry empty cells rather than a misleading status. `invoices_csv(rows, timing=None)` takes the same per-(freelancer, currency) timing map; without it both columns stay empty, so a bare `invoices_csv(rows)` call gains no invented status. Pinned by a new test file `tests/test_invoice_report_csv_timing.py` (due, overdue, upcoming, settled, no-schedule, per-currency, unassigned, `--as-of` projection, bare-call empty columns). Docs: README invoice report row + roadmap + test count, docs/src/cli.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.31.0)
- The payments CSV (`--payments-csv`) is unchanged: it exports the payment ledger, which has no per-freelancer timing to join. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.30.0] - 2026-11-13

Agency-mode slice #24: dashboard payout timing as of a projected date. Slices #14–#23 put timing on every CLI view and on both dashboard pages — but the dashboard was stuck on today: the CLI's `--as-of` had no web equivalent, so planning next week's pay run ("who will be due on Friday?") meant opening the CLI. This slice adds `?as_of=YYYY-MM-DD` to the dashboard index and the invoice detail page, projecting the Payouts section and the detail page's Freelancer row to that day.

### Added
- **Dashboard index `GET /?as_of=YYYY-MM-DD`** — the Payouts section now shows each roster freelancer's payout timing as of that day (same `payout_schedule_timing()` rollup, same due / overdue / upcoming / settled / no-schedule phrases with next or missed payout date — no new library rules). The section header reads `payout timing as of <date>` with a "back to today" link; the status-filter and Details links keep the projection so the day survives navigation. Without `?as_of=` the page is exactly as before.
- **Dashboard invoice detail `GET /invoices/{id}?as_of=YYYY-MM-DD`** — the Freelancer row's timing is projected to the same day, marked `(as of <date>)`, and the back link keeps the projection. An invalid date on either page is a clean 400, not a traceback. Pinned by a new test file `tests/test_dashboard_as_of.py` (projection to due, projection to overdue, invalid-date 400 on both pages, detail projection + back-link preservation, filter-link preservation, unprojected default). Docs: README dashboard section + roadmap + test count, docs/src/dashboard.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.30.0)
- Only the payout timing is projected: the invoice list, totals, escalation stages, and the detail page's amounts and timeline stay as-of-today. Recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.29.0] - 2026-11-12

Agency-mode slice #23: payout timing on the dashboard invoice detail. Slices #14–#22 put timing on every CLI view and on the dashboard index's Payouts section — but opening one invoice still answered only *who did the work*: telling whether that freelancer needs paying now meant going back to the index or opening the CLI. This slice puts the timing on the detail page's Freelancer row itself.

### Added
- **Dashboard invoice detail `GET /invoices/{id}`** — the Freelancer row now appends the assigned freelancer's payout timing (`payout due (next payout ...)`, `payout overdue (missed ...)`, `payout upcoming (next payout ...)`, `payout settled`, or `payout no schedule`), taken from the same `payout_schedule_timing()` rollup the index Payouts section uses — the most urgent status across the freelancer's currencies, with a currency count when more than one currency shares the winning status (`payout overdue (2 currencies, missed ...)`). No new library rules: the rollup is reused as-is. Unassigned invoices show no phrase (and no Freelancer row, as before); an assigned invoice whose freelancer has no billable invoices — a draft does not count — carries no phrase either. Read-only, like the rest of the page. Pinned by a new test file `tests/test_invoice_detail_payout_timing.py` (due, overdue, upcoming, settled, no-schedule, per-currency rollup, unassigned, draft-assigned, missing-invoice 404). Docs: README dashboard section + roadmap + test count, docs/src/dashboard.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.29.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. The dashboard has no `--as-of`: timing is always as of today. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.28.0] - 2026-11-11

Agency-mode slice #22: payout timing on the web dashboard. Slices #14–#21 put timing on every CLI view — the whole-book summary, its TOTALS lines, the per-freelancer view, the suggestion lines, the schedule lines, the roster, the receivables report, and the payout ledger — but the dashboard index still answered only *what we already sent*: telling whether a freelancer needs paying now meant opening the CLI. This slice puts the timing on the dashboard's new Payouts section.

### Added
- **Dashboard index `GET /`** — a Payouts section (between the totals and the invoice list) shows one row per roster freelancer with their payout timing (`payout due (next payout ...)`, `payout overdue (missed ...)`, `payout upcoming (next payout ...)`, `payout settled`, or `payout no schedule`), taken from the same `payout_schedule_timing()` rollup `payout schedule` and `freelancer list` use — the most urgent status across the freelancer's currencies, with a currency count when more than one currency shares the winning status (`payout overdue (2 currencies, missed ...)`). No new library rules: the rollup is reused as-is. Freelancers with no billable invoices get no row; an empty roster hides the section entirely. Read-only, like the rest of the page. Pinned by a new test file `tests/test_dashboard_payout_timing.py` (due, overdue, upcoming, settled, no-schedule, per-currency rollup, no-billable, empty roster). Docs: README dashboard section + roadmap + test count, docs/src/dashboard.md, docs/src/roadmap.md, and the changelog; generated docs pages rebuilt.

### Honest scope (0.28.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. The dashboard has no `--as-of`: timing is always as of today. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.27.0] - 2026-11-10

Agency-mode slice #21: payout timing on `payout list`. Slices #14–#20 put timing on the whole-book summary, its TOTALS lines, the per-freelancer view, the suggestion lines, the schedule lines, the roster, and the receivables report — but the payout ledger itself still answered only *what we already sent*: telling whether a freelancer in the ledger needs paying again now meant cross-checking `payout due` and `payout overdue`, or opening `agency summary`. This slice puts the timing on the ledger's newest row per freelancer.

### Added
- **`invoiceguard payout list [--freelancer ID] [--as-of DATE]`** — each freelancer's most recent payout line now appends its payout timing (`payout due (next payout ...)`, `payout overdue (missed ...)`, `payout upcoming (next payout ...)`, `payout settled`, or `payout no schedule`), taken from the same `payout_schedule_timing()` rollup `payout schedule` and `freelancer list` use — the most urgent status across the freelancer's currencies, with a currency count when more than one currency shares the winning status (`payout overdue (2 currencies, missed ...)`). No new library rules: the rollup is reused as-is. The timing appears once per freelancer, on their newest payout; older rows for the same freelancer carry no phrase. `--as-of` projects the timing as of that day; an invalid `--as-of` is a clean error, not a traceback. The ledger's row shape, ordering (newest first), and the empty-ledger behaviour are unchanged; freelancers with no billable invoices carry no timing phrase. Read-only, like the rest of the command. Pinned by a new test file `tests/test_payout_list_timing.py` (due, overdue, upcoming, settled, no-schedule, per-currency rollup, once-per-freelancer, `--as-of` projection, invalid `--as-of`, no-billable, empty ledger, `--freelancer` filter).

### Honest scope (0.27.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.26.0] - 2026-11-09

Agency-mode slice #20: payout timing on `invoice report`. Slices #14–#19 put timing on the whole-book summary, its TOTALS lines, the per-freelancer view, the suggestion lines, the schedule lines, and the roster — but the receivables report itself still answered only *who owes us*: telling whether the agency owes that freelancer a payout now meant cross-checking `payout due` and `payout overdue`, or opening `agency summary`. This slice puts the timing on the report's "by freelancer" lines themselves.

### Added
- **`reports.receivables_report()`** — each "by freelancer" entry now also carries the freelancer's id (None for the "unassigned" group), so the CLI can join payout timing onto the row; entries are grouped by name, like the client side, so two roster freelancers sharing a name share one entry and the id is the first one's.
- **`invoiceguard invoice report [--as-of DATE]`** — each "by freelancer" line now appends its payout timing (`payout due (next payout ...)`, `payout overdue (missed ...)`, `payout upcoming (next payout ...)`, `payout settled`, or `payout no schedule`), taken from the same `agency summary` rows — derived from the exact `payout due` / `overdue` rules as of `--as-of` (default: today), no new rules — per currency, matching the row's own currency, so a freelancer overdue in USD and upcoming in EUR reads overdue on the USD line and upcoming on the EUR line. `--as-of` projects the timing as of that day; an invalid `--as-of` is a clean error, not a traceback. The "unassigned" line carries no timing phrase, and freelancers with no billable invoices carry none either. Aging buckets, per-client lines, invoice rows, and the CSV outputs are unchanged; read-only, like the rest of the command. Pinned by a new test file `tests/test_invoice_report_timing.py` (due, overdue, settled, no-schedule, per-currency, unassigned, `--as-of` projection, invalid `--as-of`).

### Honest scope (0.26.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.25.1] - 2026-11-08

### Fixed
- **`freelancer show --as-of` now projects the `schedule:` header too.** The per-currency timing lines are projected with `--as-of`, but the header computed the next payout date as of today — so with `--as-of 2026-10-10` it said "next payout 2026-11-15" on the same screen where the timing line said "payout overdue (missed 2026-09-15)". The header now passes `--as-of` through to `next_payout_date()`, like `payout schedule` already does; without `--as-of` the header is unchanged (today). Pinned by a new test in `tests/test_freelancer_show_timing.py`.

## [0.25.0] - 2026-11-07

Agency-mode slice #19: payout timing on `freelancer list`. Slices #14–#18 put timing on the whole-book summary, its TOTALS lines, the per-freelancer view, the suggestion lines, and the pay-run schedule view, but the roster itself — name, contact, split — still carried no timing: telling a due freelancer from an upcoming one at a glance meant opening `payout schedule` or cross-checking `payout due` and `payout overdue`. This slice puts the timing on the roster lines themselves.

### Added
- **`invoiceguard freelancer list [--as-of DATE]`** — each roster line now appends its payout timing (`payout due (next payout ...)`, `payout overdue (missed ...)`, `payout upcoming (next payout ...)`, `payout settled`, or `payout no schedule`), taken from the same `payout_schedule_timing()` rollup `payout schedule` uses — the most urgent status across the freelancer's currencies, with a currency count when more than one currency shares the winning status (`payout overdue (2 currencies, missed ...)`). No new library rules: the rollup is reused as-is. `--as-of` projects the timing as of that day; an invalid `--as-of` is a clean error, not a traceback. The roster's contact/split rendering and the empty-roster behaviour are unchanged; freelancers with no billable invoices carry no timing phrase. Read-only, like the rest of the command.

### Honest scope (0.25.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.24.0] - 2026-11-06

Agency-mode slice #18: payout timing on `payout schedule`. Slices #14–#17 put timing on the whole-book summary, its TOTALS lines, the per-freelancer view, and the suggestion lines, but the roster's pay-run view itself still showed only the schedule and the next payout date — telling a due freelancer from an upcoming one meant cross-checking `payout due` and `payout overdue`. This slice puts the timing on the schedule lines themselves.

### Added
- **`reports.payout_schedule_timing()`** — one entry per freelancer id in `agency_summary()`: `payout_status` rolled up across the freelancer's currencies with the same precedence `agency_summary_totals()` uses per currency — `overdue` beats `due` beats `upcoming`, with `payout_currencies` counting the currency rows in the winning status, `missed_payout_date` the earliest missed run among the overdue rows, and `next_payout_date` the earliest next run among the due / upcoming rows. Unanimous `settled` or `no schedule` currencies roll up to that shared status; a settled/no-schedule mix carries no status rather than a misleading one. Freelancers with no billable invoices get no entry. Read-only, like the rest of the command.
- **`invoiceguard payout schedule [--freelancer ID] [--as-of DATE]`** — each schedule line now appends its payout timing (`payout due (next payout ...)`, `payout overdue (missed ...)`, `payout upcoming (next payout ...)`, `payout settled`, or `payout no schedule`), with a currency count when more than one currency shares the winning status (`payout overdue (2 currencies, missed ...)`). `--as-of` projects the timing as of that day; an invalid `--as-of` is a clean error, not a traceback. Sorting, the schedule rendering, and the `no freelancers on the roster` empty case are unchanged; freelancers with no billable invoices carry no timing phrase. Read-only, like the rest of the command.

### Honest scope (0.24.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.23.0] - 2026-11-05

Agency-mode slice #17: payout timing on `payout suggest`. Slices #14–#16 put timing on the whole-book summary, its TOTALS lines, and the per-freelancer view, but the answer to "what do I pay next" itself still carried no timing — telling an overdue suggestion from an upcoming one meant cross-checking `payout due` and `payout overdue`. This slice puts the timing on the suggestion lines themselves.

### Added
- **`reports.payout_suggestion_timing()`** — one entry per (`freelancer_id`, `currency`) in `payout_suggestions()`: `payout_status` derived from the exact `payout due`/`overdue` rules as of `as_of` (default: today) — `overdue` (with `missed_payout_date`), `due`, `upcoming`, or `no schedule` — plus `next_payout_date`. A suggestion row is actionable by construction, so `settled` never appears here, unlike the agency summary which also covers settled rows. Read-only, like the rest of the command.
- **`invoiceguard payout suggest [--freelancer ID] [--as-of DATE]`** — each suggestion line now appends its payout timing (`payout due (next payout ...)`, `payout overdue (missed ...)`, `payout upcoming (next payout ...)`, or `payout no schedule`), for split-share rows and no-split available rows alike. `--as-of` projects the timing as of that day; an invalid `--as-of` is a clean error, not a traceback. Sorting, amounts, and the `no payout suggestions` empty case are unchanged. Read-only, like the rest of the command.

### Honest scope (0.23.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.22.0] - 2026-11-04

Agency-mode slice #16: payout timing on `freelancer show`. Slices #14 and #15 put timing on the whole-book summary and its TOTALS lines, but drilling into one freelancer still showed only the schedule and the next payout date — answering "does this freelancer need paying now" meant cross-checking `payout due` and `payout overdue`. This slice puts the timing on the per-freelancer view itself.

### Added
- **`invoiceguard freelancer show ID [--as-of DATE]`** — each currency line now appends its payout timing (`payout due (next payout ...)`, `payout overdue (missed ...)`, `payout upcoming (next payout ...)`, `payout settled`, or `payout no schedule`), taken from the same `agency summary` rows — derived from the exact `payout due`/`overdue` rules as of `--as-of` (default: today), no new rules. Freelancers with no billable invoices carry no timing phrase, matching the summary's no-currency row. `--as-of` with an invalid date is a clean error, not a traceback. Contact, earnings, invoice, and payout lines are unchanged; the schedule line still shows the next payout date. Read-only, like the rest of the command.

### Honest scope (0.22.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.21.0] - 2026-11-03

Agency-mode slice #15: payout timing on the agency summary TOTALS. Slice #14 put timing on every per-freelancer currency line but left the TOTALS lines answering only *how much* — an agency owner glancing at the whole-book totals still could not tell whether anything in a currency was overdue without reading every freelancer section. This slice rolls the timing up.

### Added
- **`reports.agency_summary_totals()`** — per-currency totals across the summary (money totals plus the suggested total, as before, now from the library instead of recomputed in the CLI) with a payout-timing rollup derived from the rows' own `payout_status` values — no new rules. The rollup reports the single most urgent status in the currency: `overdue` beats `due` beats `upcoming`, with the freelancer count in that status and the earliest missed run (overdue) or earliest next run (due/upcoming). When nothing is actionable and every row agrees, the rollup is that shared status (`settled` or `no schedule`); when nothing is actionable and the rows disagree — some settled, some unscheduled — the currency carries no status rather than a misleading one. Read-only, like the summary itself.
- **`invoiceguard agency summary` TOTALS lines** now append the rollup (`payout overdue (2 freelancers, missed 2026-11-10)`, `payout due (1 freelancer, next payout ...)`, `payout upcoming (...)`, `payout settled`, or `payout no schedule`); a mixed settled/no-schedule currency prints no timing phrase. Per-freelancer lines and all money totals are unchanged; `--freelancer` and `--as-of` filter and project the rollup exactly as they do the rows.

### Honest scope (0.21.0)
- A rollup, not a new rule: the timing still comes from the `payout due`/`overdue` derivations — the totals line never invents a status the rows do not already carry. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.20.0] - 2026-11-02

Agency-mode slice #14: payout timing on the agency summary. Slices #10–#13 built the pay-run views (`due`, `overdue`) and their settlements, but the whole-book view still answered only *how much* — checking whether a freelancer's payout was due, overdue, or upcoming meant cross-checking two more commands. This slice puts the timing on the summary itself.

### Added
- **`reports.agency_summary()` gains `as_of` and payout-timing fields** — every row now carries `payout_cadence`, `payout_day`, and `next_payout_date` from the freelancer's schedule, plus a per-currency `payout_status` derived from the exact rules of `payout_due()` and `payout_overdue()` as of `as_of` (default: today): `overdue` (a missed run still owed, with its `missed_payout_date`), `due` (the pay run has arrived), `upcoming` (scheduled, actionable, next date still ahead), `no schedule` (no schedule set, so nothing can be due), or `settled` (scheduled, but nothing actionable left). Rows with no billable invoices carry the schedule fields but no status — there is no per-currency row to be due or overdue. Read-only, like the rest of the summary.
- **`invoiceguard agency summary [--freelancer ID] [--as-of DATE]`** — each currency line now appends its payout timing (`payout due (next payout 2026-11-15)`, `payout overdue (missed 2026-10-15)`, `payout upcoming (next payout ...)`, `payout settled`, or `payout no schedule`); `--as-of` projects the timing as of that day, matching `payout due`/`overdue`. An invalid `--as-of` is a clean error, not a traceback.

### Honest scope (0.20.0)
- A view, not a workflow: the status reports timing; recording a due or overdue payout is still `payout settle-due` / `payout settle-overdue`. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.19.0] - 2026-11-01

Agency-mode slice #13: settle the missed pay runs. Slice #12 listed the missed runs; this slice records them — `payout settle-due` settles only the pay run whose date has arrived, and a freelancer paid at or after the missed run was never overdue at all, so a missed run previously had no scoped way to be recorded short of settling the whole book.

### Added
- **`invoiceguard payout settle-overdue [--freelancer ID] [--as-of DATE] [--currency CUR] [--method M] [--paid-at DATE] [--dry-run]`** — one payout per overdue row, for the suggested amount, through the same over-payout guard as `payout record` (an overdue suggestion can never exceed what was collected and unpaid, so the guard cannot refuse a fresh one). A freelancer due on the pay-run date itself stays in `payout settle-due`; a freelancer paid at or after the missed run produces no row. No-split freelancers are skipped with a count, never guessed at — set a split with `freelancer set-split`, or record their amount by hand with `payout record`. `--dry-run` prints what would be recorded and writes nothing; an empty roster prints `no freelancers on the roster`, a roster with nothing overdue prints `no overdue payouts`, and a second settle-overdue on the same missed runs is a no-op, so nothing is double-paid. Bookkeeping only — nothing is transferred.
- **`payout overdue` now points at `payout settle-overdue`** (its own docstring and the library docstring) as the scoped way to record the missed runs it lists.

### Honest scope (0.19.0)
- Bookkeeping, not money movement: settling records payouts, never transfers anything, and the settled amounts are exactly the overdue suggestions. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.18.0] - 2026-10-31

Agency-mode slice #12: payouts overdue from a missed pay run. Slice #10 looked forward — the morning after a missed pay run it reported "no payouts due", even though money was still owed past a pay date. This slice looks backward: the missed run itself.

### Added
- **`invoiceguard payout overdue [--freelancer ID] [--as-of DATE]`** — one row per freelancer per currency: a scheduled freelancer whose most recent scheduled payout date has already passed, who still has an actionable payout — a still-due split share, or the still-held balance when no split is set (labeled as available, never guessed at) — and who has nothing recorded for that currency since that missed date. Rows sort by missed payout date (earliest first), then largest amount first. An empty roster prints `no freelancers on the roster`; a roster with nothing overdue prints `no overdue payouts`; an unknown `--freelancer` id is a clean error, not a traceback. Read-only — nothing is recorded or transferred.
- **`reports.payout_overdue()` / `reports.previous_payout_date()`** — the same view for library callers (rows carry `missed_payout_date` alongside `next_payout_date`), and the scheduled date immediately before the next one: weekly steps back 7 days, monthly steps back to the previous month's clamped day (a day-31 schedule's previous date after a 30-day month is that month's last day; February clamps to Feb 28/29).

### Honest scope (0.18.0)
- Overdue means exactly this: a scheduled pay date passed with an actionable payout and nothing recorded since that date. A freelancer due on the pay-run date itself is due, not overdue — they appear in `payout due`, never here. Money collected since the last run waits for the next run; it is not a missed run. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.17.0] - 2026-10-30

Agency-mode slice #11: settle the pay run. Slice #10 listed the pay run; this slice records it — `payout settle` settles the whole book, but an agency runs payouts on a schedule, and this settles only the freelancers whose payout date has arrived.

### Added
- **`invoiceguard payout settle-due [--freelancer ID] [--as-of DATE] [--within-days N] [--currency CUR] [--method M] [--paid-at DATE] [--dry-run]`** — one payout per due row, for the suggested amount, through the same over-payout guard as `payout record` (a due suggestion can never exceed what was collected and unpaid, so the guard cannot refuse a fresh one). A freelancer whose schedule says next week keeps their suggestion until their own pay run; `--within-days 7` settles this week's pay run. No-split freelancers are skipped with a count, never guessed at — set a split with `freelancer set-split`, or record their amount by hand with `payout record`. `--dry-run` prints what would be recorded and writes nothing; an empty roster prints `no freelancers on the roster`, a roster with nothing due prints `no payouts due`, and a second settle-due on the same pay run is a no-op, so nothing is double-paid. Bookkeeping only — nothing is transferred.
- **`payout due` now points at `payout settle-due`** (its own docstring and the library docstring) as the way to record the run it lists.

### Honest scope (0.17.0)
- Bookkeeping, not money movement: settling records payouts, never transfers anything, and the settled amounts are exactly the due suggestions. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.16.0] - 2026-10-29

Agency-mode slice #10: payouts due on the pay run. Slices #6 and #9 answered *what* to pay and *when* to pay separately; this slice joins them — the pay run is the scheduled freelancers whose payout date has arrived and who still have something actionable to pay.

### Added
- **`invoiceguard payout due [--freelancer ID] [--as-of DATE] [--within-days N]`** — one row per freelancer per currency: a scheduled freelancer whose next payout date falls on the pay-run date (default: today), or within N days after it with `--within-days` (so `--within-days 7` is this week's pay run), and who has an actionable payout suggestion — a still-due split share, or the still-held balance when no split is set (labeled as available, never guessed at). Rows sort by next payout date (earliest first), then largest amount first. An empty roster prints `no freelancers on the roster`; a roster with nothing due prints `no payouts due`; an unknown `--freelancer` id or a negative `--within-days` is a clean error, not a traceback. Read-only — nothing is recorded or transferred.
- **`reports.payout_due()`** — the same view for library callers: suggestion rows enriched with `payout_cadence`, `payout_day`, and `next_payout_date`; raises ValueError for an unknown freelancer or a negative `within_days`.

### Honest scope (0.16.0)
- A view, not a payment: it lists the pay run; recording it is still `payout settle` (split freelancers) or `payout record` (by hand). Unscheduled freelancers are never due — there is no date to be due by — and a due date with nothing to pay (settled, paid past the share, nothing collected) produces no row. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.15.0] - 2026-10-28

Agency-mode slice #9: per-freelancer payout schedules. Slices #4–#8 recorded what the agency paid and what it should pay next; this slice answers *when* it pays — each roster freelancer gets a recurring schedule, and the next payout date is derived from it.

### Added
- **`invoiceguard freelancer set-schedule ID --cadence weekly|monthly --day N`** — weekly takes an ISO weekday 1–7 (Monday–Sunday), monthly a day of month 1–31. Validated before anything is written (bad cadence, out-of-range day, and unknown freelancer are clean errors, not tracebacks); `freelancer clear-schedule ID` clears it. New `payout_cadence`/`payout_day` columns on `freelancers` (additive migration — pre-v0.15.0 databases get them on connect).
- **`invoiceguard payout schedule [--freelancer ID] [--as-of DATE]`** — one row per roster freelancer: schedule plus next payout date, earliest first, unscheduled freelancers listed last as `no schedule set`; an empty roster prints `no freelancers on the roster`. Read-only — nothing is recorded or transferred.
- **`reports.payout_schedule()` / `reports.next_payout_date()`** — the same derivation for library callers. A monthly day-31 schedule clamps to the month's last day (Feb 28, or Feb 29 in a leap year); a payout day that falls on the as-of date counts as the next payout date.
- **`freelancer show`** prints the schedule line (`schedule: monthly on day 15 (next payout 2026-11-15)`) when one is set.

### Honest scope (0.15.0)
- A schedule, not a payment: it records nothing, approves nothing, and transfers nothing — `payout settle` still records the actual payouts, on whatever date the agency runs it. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.14.0] - 2026-10-27

Agency-mode slice #8: agency summary. `payout suggest` lists only actionable rows and `freelancer show` covers one freelancer at a time; this slice is the whole book at a glance — every roster freelancer, every currency, plus per-currency totals across the roster.

### Added
- **`invoiceguard agency summary [--freelancer ID]`** — one section per roster freelancer: per-currency billed, collected, outstanding, paid out, and still owed, plus the suggested payout (the still-due split share) where one exists; freelancers with no billable invoices print `no billable invoices`. A `TOTALS` section sums each currency across the roster, with a suggested total where any split freelancer still has a share due. Read-only — nothing is recorded or transferred. An empty roster prints `no freelancers on the roster`; an unknown `--freelancer` id is a clean error, not a traceback.
- **`reports.agency_summary()`** — the same view for library callers: `freelancer_earnings()` rows enriched with `suggested_cents` (None when there is no share suggestion — no split set, settled, paid past the share, or no billable invoices).

### Honest scope (0.14.0)
- A view, not a workflow: the summary reports the book; it records nothing, approves nothing, and transfers nothing. Suggested totals count only split freelancers with a still-due share — no-split freelancers contribute their still-owed balance to the totals but no suggestion. No per-freelancer payout schedules, and multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.13.0] - 2026-10-26

Agency-mode slice #7: payout settlement. Slice #6 answered "what do I pay next?" per freelancer per currency; this slice records that answer — one payout per suggestion, for the suggested amount, through the same over-payout guard as `payout record`.

### Added
- **`invoiceguard payout settle [--freelancer ID] [--currency CUR] [--method M] [--paid-at DATE] [--dry-run]`** — records every suggested payout (split freelancers only) in one command. Each payout goes through the existing guard, so a fresh suggestion can never be refused; `--dry-run` prints what would be recorded and writes nothing. Freelancers with no split set are skipped with a count, never guessed at — set a split with `freelancer set-split`, or record their amount by hand with `payout record`. A second settle is a no-op: a settled freelancer produces no further suggestion, so nothing is double-paid.

### Honest scope (0.13.0)
- Bookkeeping, not money movement: settling records payouts, never transfers anything, and the settled amounts are exactly the suggestions — the agency still decides when to run it. No per-freelancer payout schedules, and multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.12.0] - 2026-10-25

Agency-mode slice #6: payout suggestions. Slice #5 stored each freelancer's revenue split and reported their still-due share; this slice turns that into the answer to "what do I pay next?" — per freelancer, per currency, derived from collected earnings minus payouts already recorded.

### Added
- **`invoiceguard payout suggest [--freelancer ID]`** — prints one line per freelancer per currency: with a split set, the still-due share (`70% of 900.00 collected, 250.00 already paid`); without a split, what the agency still holds for them, labeled as available rather than suggested, with a pointer to `freelancer set-split`. Sorted largest suggestion first; `no payout suggestions` when nothing is actionable. Read-only — nothing is recorded or transferred, and `payout record` still enforces its own over-payout guard.
- **`reports.payout_suggestions()`** — the same derivation for library callers: rows carry `suggested_cents` (None when no split is set), `available_cents`, and the collected/paid/expected figures behind the number.

### Honest scope (0.12.0)
- A suggestion is not an approval and not a transfer: the agency still records the payout itself, and the guard still allows paying past the split share (up to what was collected) — a freelancer paid past their share simply stops appearing in suggestions instead of going negative.
- No per-freelancer payout schedules, and multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.11.0] - 2026-10-24

Agency-mode slice #5: per-freelancer revenue-split percentage. Slice #4 recorded what the agency paid each freelancer, guarded by what was collected — but it could not answer how much of the collected money was the freelancer's share in the first place. A revenue split (e.g. 70%) is now stored on each roster freelancer (`split_pct`, NULL = no split set); `freelancer show` and `reports.freelancer_earnings()` then report, per collected currency, the freelancer's expected share of what was collected and what is still due to them after recorded payouts.

### Added
- **Revenue splits** — `invoiceguard freelancer add --split-pct 70` and `invoiceguard freelancer set-split 1 --split-pct 65.5` (validated 0–100 before anything is written, via the same `_validate_percent` rule as `--deposit-pct`). A `split_pct` column on `freelancers` (additive migration — pre-v0.11.0 databases get it on connect); `freelancer list` prints the split after the phone; `freelancer show` prints a `split: 70% of collected earnings` header line and each currency line appends `share (70%): 630.00, still due to freelancer: 630.00`.
- **`freelancer_earnings()` split fields** — every row carries `split_pct`, plus (only when a split is set) `expected_payout_cents` (collected × split, half-up to the cent with the project's Decimal money convention, shared helper `db.pct_of_cents` — previously a CLI-local copy) and `still_due_to_freelancer_cents` (expected minus paid out). Without a split both are None; no-currency rows report zero.

### Honest scope (0.11.0)
- Bookkeeping, not money movement: the split is recorded and reported, never transferred, and nothing is derived automatically — no per-freelancer payout schedules, no automatic payout suggestions. The payout guard is unchanged: it still refuses any payout larger than what was collected, even when that exceeds the freelancer's split share (over-paying against the split is the agency's own business, and the bookkeeping shows it).
- The split applies to what was *collected* from the payment ledger, not what was billed — the agency shares received money, not invoices. Multi-freelancer workspaces remain the unshipped half of agency mode.

## [0.10.0] - 2026-10-23

Agency-mode slice #4: freelancer payout tracking. Slice #3 showed what each freelancer's work billed and collected; this slice records the other half of the ledger — what the agency has actually sent each freelancer, per currency.

### Added
- **Freelancer payouts** — `invoiceguard payout record --freelancer ID --amount X [--currency USD] [--paid-at YYYY-MM-DD] [--method TEXT] [--note TEXT]` and `invoiceguard payout list [--freelancer ID]`. A `payouts` table (additive migration — existing databases get it on connect); per-currency totals in `freelancer show` (earnings lines now print *paid out* and *still owed*, followed by a payouts section) and in `reports.freelancer_earnings()` rows (`paid_out_cents`, `still_owed_cents` = collected − paid out).
- **Over-payout guard** — a payout is refused with a clear error when it exceeds what the agency has collected for that freelancer in that currency (payment-ledger sums against their attributed non-draft/void invoices, minus payouts already recorded). You cannot pay out money you have not received. Currency codes are normalized to upper-case; invoice-currency matching is case-insensitive; `--paid-at` accepts `YYYY-MM-DD` or full ISO-8601.

### Honest scope (0.10.0)
- Bookkeeping, not money movement: payouts are recorded, never transferred, and nothing is derived automatically — no revenue-split percentages, no per-freelancer payout schedules. Those, plus multi-freelancer workspaces, remain the unshipped half of agency mode.
- There is no per-freelancer "rate" in the roster: the guard is purely "collected − already paid out", so a payout ledger for work that was never invoiced cannot exist.

## [0.9.0] - 2026-10-22

Agency-mode slice #3: freelancer earnings. Until now the freelancer side of the product could only show *receivables* — who still owes what (`invoice report --freelancer` / the "by freelancer" breakdown). That answers half of an agency owner's question; the other half is what each freelancer's work actually brought in.

### Added
- **Freelancer earnings** — `reports.freelancer_earnings()` computes per-freelancer, per-currency totals: *billed* (attributed invoices in a real state — drafts and voids are not bills), *collected* (payment-ledger sums against those invoices), *outstanding* (billed minus collected). Every roster freelancer appears, even with no billable invoices; unattributed invoices belong to nobody and are counted nowhere; currencies are never mixed. Read-only.
- **`invoiceguard freelancer show <id>`** — a freelancer's contact details, per-currency earnings (billed / collected / outstanding, invoice count), and the attributed invoice lines (client, amount, collected, outstanding, status). `freelancer show 999` is a clean error, not a traceback.

### Honest scope (0.9.0)
- Earnings are bookkeeping, not payouts: nothing is recorded against what the agency owes the freelancer, and no money moves. Per-freelancer revenue splits / payout tracking remain the unshipped half of agency mode.
- A freelancer with invoices in two currencies gets one row per currency, same as the receivables report's per-currency rule.

## [0.8.4] - 2026-10-22

Every fix in this release comes from an external hands-on review: the reviewer installed the package with stripe 15.6.1 and ran it live. The headline bug was critical — under stripe>=15 the webhook silently never recorded payments, so paid clients kept receiving dunning notices.

### Fixed
- **CRITICAL — Stripe webhook never recorded payments under stripe>=15.** The dashboard handler tested `isinstance(metadata, dict)`; stripe 15 returns session metadata as a `StripeObject` (not a dict, no `.get()`), so the invoice-id hint was silently dropped. The fallback could not save it either: the CLI stores the Payment Link id (`plink_...`) in `stripe_session_id`, while the webhook looks up the checkout session id (`cs_...`), which never matches. The webhook still returned 200, Stripe never retried, the invoice stayed `sent`, and dunning kept emailing clients who had paid. Metadata is now normalized via `.to_dict()` when needed (dict payloads from older SDKs pass through), in both the dashboard handler and `webhooks.handle_event` (which also crashed with `AttributeError` on a real `StripeObject` event — it had only ever been fed dicts by tests). The pre-0.8.4 test suite missed all of this because its seeded invoices stored the `cs_...` id it later looked up; the new regression test seeds the production shape (`plink_...` stored, `cs_...` in the event). The stripe pin was *not* narrowed — the code fix is robust across stripe 12–15.
- **`payment_status` was never checked.** A `checkout.session.completed` event with `payment_status=unpaid` (delayed methods such as ACH, where the money has not arrived) marked the invoice paid. Both webhook paths now record a payment only when `payment_status` is `paid` / `no_payment_required` (or absent, for older payloads), and `checkout.session.async_payment_succeeded` — the event Stripe sends when a delayed payment actually clears — is handled by the same path.
- **Late fees doubled at month-end.** Accrual counted *started calendar months*: an accrual starting Jan 31 charged two months of fees on Feb 1 (2.02x for one day), and a 12-month span could be charged 13 fees — more than contract §3's "% per month" promises. Accrual now counts *full monthly periods elapsed* since the accrual start, anchored on the start day (month-end clamped): Jan 31 → 1 month on Feb 28, exactly 12 in a year. Contract §3 wording, the CLI `late-fees` text, and the docs now describe the same math.
- **`sign-status` contradicted the acknowledgment state.** It reported only the e-signature track, so a project already acknowledged via the deposit link (`--ack` / `project ack`) still showed a bare "awaiting signature" / "no signature request yet". It now reports both tracks: signature state plus the deposit-link acknowledgment (with its timestamp).
- **Failed `invoice create` left orphan drafts.** The draft row was inserted before the Stripe payment link was created; every failure (missing key, Stripe API error) left a draft behind (3 failures → 3 drafts), and API errors surfaced as a traceback. The draft is now rolled back on any link-creation failure (`DB.delete_invoice`, guarded to payment-less drafts only) and the error prints as a clean one-line CLI message.
- **A paid invoice could be voided.** `invoice void` had no status check. It now refuses paid invoices (their ledger payments stand; refund through Stripe) and already-void invoices, with a clear error.
- **Dunning emails leaked template markup.** Bodies contained the template file's literal `Subject:` line, and plain-text emails showed literal markdown (`**$1,250.00**`). Bodies are now cleaned at send time (`mail.to_plain_text`: legacy `Subject:` line dropped, `**` emphasis stripped — existing user templates in `~/.invoiceguard/templates` are fixed without re-running `init`), and the shipped templates no longer carry a `Subject:` line at all (subjects live in `dunning.SUBJECTS`).

### Added
- `scripts/smoke_install.py` — fresh-install smoke check: builds the wheel, installs it into a clean venv, and runs a real flow (init → client → project → invoice create with Stripe mocked at the SDK boundary → a genuinely signed webhook event verified through `stripe.Webhook.construct_event`). This is the class of check that would have caught the stripe-15 webhook bug; the repo has no CI workflows to wire it into, so it is a script to run before releases (it was run for this one — see the release notes).

### Docs
- Version/test-count drift corrected (README test paragraph, docs CLI/install pages that still said v0.1.0, site test count). Webhook docs now cover the `async_payment_succeeded` event and the delayed-payment behavior; the endpoint setup step lists both events.

### Honest scope (0.8.4)
- The `stripe>=12,<16` range is unchanged: the fix makes the code correct under every supported major instead of pinning around the bug.
- Late-fee change is prospective math, not a migration: previously computed *displays* change (fees are computed on read, never stored), and a balance inside its first accrual month now shows 0 fees until the first full month elapses — that is the contract's "% per month" read literally.
- The dashboard keeps its own inline webhook handler (web/main.py) alongside the standalone `webhooks.handle_event`; both paths got the same metadata + payment_status fixes and both are covered by regression tests.

## [0.8.3] - 2026-10-21

E-signature contract snapshot: the signing flow now freezes the contract text at the moment the link is created.

### Fixed
- Verified: `sign-request` hashed and rendered the project's *live* contract text — so if the contract was edited after the signing link was created but before the client signed, the SHA-256 tamper-evidence hash covered text the client never saw (and the signing page itself showed the edited text). The project's contract markdown is now snapshotted into the signature request row at creation time (`signatures.contract_md`); the signing page renders that snapshot and the signature hash covers it. Requests created before v0.8.3 have no snapshot and fall back to the current project text; existing databases get the new column additively on connect (closes the e-signature TOCTOU audit finding).

### Honest scope (0.8.3)
- Snapshot-side only: nothing about token lifecycle, signature capture, or acknowledgment semantics changed. Re-running `sign-request` while a request is pending still returns the existing link with its original snapshot.

## [0.8.2] - 2026-10-20

`project create` now validates its financial options: `--deposit-pct` and `--late-fee-pct` must be within 0–100, and `--late-fee-grace-days` must be 0 or more. Out-of-range values are refused with a clear error before anything is written.

### Fixed
- The percentages flow into the stored project *and* the generated contract text, so values like `--deposit-pct 500` or `--late-fee-pct -3` previously produced nonsense money math and a nonsense late-fee clause in a legal document. They are now rejected up front (closes the remaining CLI financial-validation audit finding; `--amount` already validated, dunning thresholds already validated).

### Honest scope (0.8.2)
- Validation is CLI-side only: projects created by older versions with odd values are untouched (no migration, no rewrite of stored data).

## [0.8.1] - 2026-10-17

CSV formula-injection fix: the `--csv` and `--payments-csv` exports now neutralize spreadsheet formula injection (OWASP).

### Fixed
- Verified: client names, project titles, payment methods, and payment notes are free text, and the invoice/payment CSV exports wrote them raw — a value like `=HYPERLINK("http://evil","x")` in a client name would execute as a live formula when the accountant opened the export in Excel/LibreOffice (classic CSV injection). Any exported cell whose first character is `=`, `+`, `-`, `@`, `|`, `%`, tab, or carriage return now gets a leading single quote (`'=HYPERLINK(...)`) — spreadsheet apps render it as plain text, so benign values are unchanged and only the formula interpretation is defused.

### Honest scope (0.8.1)
- Export-side only: the database, CLI parsing, and every other output (text report, dashboard) are untouched. The quote is a text marker that spreadsheet apps do not display; raw CSV readers see the literal leading `'`.

## [0.8.0] - 2026-10-16

Freelancer roster + invoice attribution — the second agency-mode slice.

### Added
- `freelancers` table: `invoiceguard freelancer add --name N [--email E] [--phone P]`, `list`, `set-email`, `set-phone` (mirrors the `client` commands; phone must be E.164).
- `invoiceguard invoice assign <id> --freelancer <id>` attributes an invoice to a freelancer; `--none` clears it. Attribution is pure bookkeeping — it never changes dunning, payments, or invoice status.
- `invoice list --freelancer ID` filters to one freelancer's invoices; the list shows the attributed freelancer's name.
- `invoice report --freelancer ID` restricts the receivables report to one freelancer; the text report gains a "by freelancer" breakdown (shown when more than one attribution group has open invoices); the invoice CSV gains a trailing `freelancer` column.
- The dashboard shows the attributed freelancer on the invoice list and the invoice detail page.
- Existing databases get the `freelancers` table and `invoices.freelancer_id` additively on connect (NULL = unattributed, no data touched).

### Honest scope (0.8.0)
- No per-freelancer logins, permissions, or separate workspaces: attribution records *whose* work an invoice is so receivables can be broken down per freelancer. The remaining half of agency mode (multi-freelancer workspaces) is still unshipped.

## [0.7.1] - 2026-10-15

Stripe webhook idempotency: processed Stripe event ids are now recorded in a `webhook_events` table, so a retried delivery of an already-handled event is ignored (`ignored:duplicate_event`) instead of re-recorded.

### Fixed
- A retried `checkout.session.completed` webhook for a *partial* payment recorded a second payment row — replaying the same half-payment event could mark the invoice fully-paid on half the money. Same event id delivered twice is now a no-op on the second delivery; distinct event ids for the same invoice are still processed normally.
- Existing databases get the `webhook_events` table additively on connect (no manual migration).

## [0.7.0] - 2026-10-14

Per-client dunning policies: the first agency-mode slice — each client can now have its own dunning stage thresholds.

### Added
- `clients` table gains `dunning_day1`, `dunning_day7`, `dunning_day15` (additive migration — existing databases get the columns on connect; NULL = global default, 0 = stage disabled for that client).
- `invoiceguard client set-dunning-policy <id> [--day1 N] [--day7 N] [--day15 N] [--reset]` — set per-client days-overdue thresholds; `0` disables a stage for that client; `--reset` restores the global defaults (1/7/15). Thresholds must keep escalation order (day1 -> day7 -> day15); negative values are rejected and nothing is written on validation failure.
- `check-due` resolves each invoice's client policy per invoice: custom thresholds fire at the custom day, disabled stages are skipped in escalation, and an invoice whose client has every stage disabled is reported as `skipped dunning (invoice #N)` — nothing recorded, so the opt-out stays visible instead of silently vanishing.
- `client list` prints a `[dunning ...]` marker for clients with overrides (e.g. `[dunning day1=3 day15=off]`).

### Honest scope (0.7.0)
- Only *when* (or whether) a stage fires changes: stage names, subjects, templates, and idempotency are untouched. The day-15 SMS/WhatsApp escalation still follows the `day15` stage — with a custom threshold it fires at the custom day. Multi-freelancer workspaces (the other half of agency mode) are still unshipped.

## [0.6.5] - 2026-10-13

Dunning send-failure isolation: one invoice's email send failing no longer aborts the whole `check-due` run.

### Fixed
- `check_due` called `send_email()` unguarded inside the per-invoice loop — verified: a transient SMTP failure (`smtplib.SMTPException`) on the first overdue invoice propagated out and aborted the run, so every *other* overdue invoice never got its stage that run. This is the same "one bad invoice kills the collections run" shape as the email-less-client crash fixed in 0.6.3 (different trigger). Sending is now isolated per invoice: a failed send is reported in the result dict as `send_error` (and printed as `failed <stage> (invoice #N): ...` by the CLI), nothing is recorded for that invoice, and the rest of the batch still goes out. Catches `OSError` (SMTP, socket, and SSL errors) plus `ValueError`.

### Honest scope (0.6.5)
- Nothing is recorded for a failed-send invoice — no `dunning_events` row and no status flip to `overdue` — so the stage is simply retried on the next run (the same convention as the 0.6.3 email-less skip). Successful sends are unchanged: send → record → flip, one email per invoice per run, stages still idempotent.

## [0.6.4] - 2026-10-12

Webhook void-safety: a Stripe webhook no longer resurrects a voided invoice.

### Fixed
- The dashboard's `mark_invoice_paid` (the `POST /webhooks/stripe` handler) recorded the Stripe payment and marked the invoice `paid` even when the invoice had been voided — verified: a void invoice came back as `paid` with a `stripe` ledger row. The core `record_payment()` refuses payments on void invoices (`ValueError: invoice #N is void`), so a late or duplicate webhook after `invoice void` resurrected the invoice against the rest of the system's rules. Void is now terminal on the webhook path too: the event is ignored, nothing is recorded, and the invoice stays void.

### Honest scope (0.6.4)
- Only the webhook path changed: sent / partially-paid / overdue invoices are marked paid exactly as before, and already-paid invoices stay idempotent. If Stripe money actually lands against a voided invoice, it is not recorded in InvoiceGuard — void it only when you mean no payment should count.

## [0.6.3] - 2026-10-11

Dunning robustness: one email-less client no longer kills the whole collections run.

### Fixed
- `check-due` crashed with `ValueError: recipient address is empty` when any overdue invoice's client had no email on file — and since `client add` makes `--email` optional, this was easy to hit. The exception aborted the entire run, so every *other* overdue invoice never got its email. Overdue invoices with no client email are now skipped with a reported reason (`skipped day7 (invoice #3): client has no email on file — add one with `invoiceguard client set-email``); nothing is recorded for a skipped invoice, so the stage goes out on the next run once an email is on file, and the rest of the batch is unaffected.

### Added
- `invoiceguard client set-email <id> [--email ...]` — set or clear a client's email address (dunning). Previously there was no way to add an email to a client created without one (`set-phone` existed; `set-email` did not).

### Honest scope (0.6.3)
- Only the email path changes: a skipped stage means its email never went out, so neither the day-15 SMS/WhatsApp escalation (which is only ever *in addition* to the email) nor any dunning-event row is recorded for it. Invoice state is otherwise untouched; existing databases need no migration. Agency mode remains unshipped.

## [0.6.2] - 2026-10-10

Contract deposit fix: the amount printed in the contract now always matches the deposit you are quoted and charged.

### Fixed
- `render_contract` computed the deposit shown in the contract with binary float + Python's `round()` (banker's rounding) — so a $10.05 project at 50% produced a contract saying a **$5.02** deposit was due, while `project create` quoted $5.03 and the deposit invoice charged $5.03. The contract is the document the client signs (and whose exact text is SHA-256-hashed as tamper evidence at signing), so its deposit figure must be the charged one. It now uses the project's money convention — `Decimal`, rounded half-up to the cent — the same rule the CLI amount parsing (0.6.1) and the late-fee calculator already used.

### Honest scope (0.6.2)
- Only the deposit figure in newly generated contract text changes, and only on half-cent boundaries (odd-cent totals at a 50% split, etc.) where the old rounding disagreed. Stored contracts, invoice amounts, and the payment ledger are untouched; existing databases need no migration. Agency mode remains unshipped.

## [0.6.1] - 2026-10-09

CLI money-parsing fix: amounts you type are no longer a cent low.

### Fixed
- `project create --amount`, `invoice create --amount`, and `invoice record-payment --amount` parsed your input through binary float + Python's `round()` (banker's rounding), so `--amount 1.005` was recorded as 100 cents instead of 101 and `record-payment --amount 10.075` booked 1007 instead of 1008. Amounts are now parsed with `Decimal` and rounded half-up to the cent — the same rounding convention the late-fee calculator (contract §3) already documented — and the deposit/final split uses the same half-up rule (deposit + final still sum to the project total).
- Non-positive or non-numeric amounts (`--amount 0`, `--amount -50`, `--amount abc`) are now rejected with a clear error before anything is written; previously `project create --amount -50` silently created a negative-cents project, and a bad `invoice create --amount` could leave an orphan draft invoice behind when Stripe rejected the amount.

### Honest scope (0.6.1)
- Only CLI-entered amounts changed; stored cents, the payment ledger, and Stripe session amounts were already exact integers and are untouched. Existing databases need no migration. Agency mode remains unshipped.

## [0.6.0] - 2026-10-08

Receivables report: see who owes you what, and how late — without opening the dashboard — and get the numbers out as CSV for your accountant.

### Added
- `invoiceguard invoice report [--as-of YYYY-MM-DD]` — read-only aging report over open invoices (sent / overdue / partially-paid with a balance): outstanding totals grouped into Current / 1-30 / 31-60 / 61-90 / 90+ day buckets, every open invoice oldest-debt-first with its accrued late fees (same contract §3 calculator as `invoice late-fees`), per-client totals, and grand totals. "Open" and "outstanding" mean exactly what the dashboard totals mean (invoice amount minus the payment-ledger sum); drafts, paid, and void invoices are excluded. Totals are per currency — different currencies are never added together.
- CSV export for accounting: `--csv PATH` writes one row per open invoice (amount, paid, outstanding, accrued late fees, total due, due date, days overdue); `--payments-csv PATH` writes the full payment ledger (date, invoice, client, method, amount, note) for bank reconciliation — including payments on invoices that are now paid in full. Passing `-` as the path prints that CSV to stdout instead of the text report.

### Honest scope (report)
- Read-only: the report never sends anything and never changes invoice state. Late fees are computed as of the report date on the current outstanding balance — the same caveat as `invoice late-fees` (no day-by-day amortization of payments made mid-accrual). Aging buckets are whole days overdue as of the report date; an invoice due today counts as Current. Agency mode (multi-freelancer workspaces, per-client dunning policies) remains unshipped — it is a multi-day change and was deliberately not rushed into this release.

## [0.5.0] - 2026-10-07

SMS / WhatsApp escalation: the day-15 formal notice can now also reach the client as a short message, not just email — clients read texts even when they let email pile up.

### Added
- `messaging.py`: SMS and WhatsApp sending via the Twilio Messages API (stdlib urllib, no new dependency). Same context variables as the dunning emails, rendered into one short message (`InvoiceGuard: Hi {client_name}, ... Pay here: {pay_url}`).
- Automatic escalation: when `messaging:` is configured and the client has a phone on file, `check-due` sends the day-15 stage by message as well as email. Sends are recorded in a new `message_events` ledger and happen at most once per stage per invoice (same idempotency rule as email). A failed message never blocks the email — the failure is reported in the `check-due` output instead.
- `invoiceguard invoice notify <id> [--channel sms|whatsapp] [--dry-run]` — send a payment reminder for one invoice right now; `--dry-run` renders and prints the exact message without sending (works without Twilio credentials).
- Client phone numbers: `invoiceguard client add --phone +15551234567`, `invoiceguard client set-phone <id> [--phone ...]`, shown in `client list`. Numbers are normalized to E.164 form and validated on entry.
- Config: new `messaging:` section (`channel`, `account_sid`, `auth_token`, `from_number`), overridable via `INVOICEGUARD_TWILIO_ACCOUNT_SID` / `INVOICEGUARD_TWILIO_AUTH_TOKEN` / `INVOICEGUARD_TWILIO_FROM_NUMBER` env vars. Additive DB migration adds `clients.phone` + the `message_events` table to existing databases automatically.

### Honest scope (messaging)
- Requires your own Twilio account (credentials + a Twilio number); sends are verified with the HTTP layer mocked — no live Twilio call was made in this build environment. WhatsApp additionally requires a Twilio-approved WhatsApp sender on your account. Message/phone-number availability and pricing are Twilio's, and consumer-messaging consent rules vary by jurisdiction — only message clients who agreed to be contacted.

## [0.4.0] - 2026-10-06

Late-fee accrual calculator: the contract has always promised "{late_fee_pct}% per month, compounding monthly" after the grace period — now InvoiceGuard computes the actual number.

### Added
- `invoiceguard invoice late-fees <id> [--as-of YYYY-MM-DD]` — shows the accrual breakdown: due date, grace window, rate, months billed, accrued fees, and total due. Formula: fees start accruing the day after the grace period ends; each started calendar month charges one full monthly fee, compounding on the running balance (outstanding + fees accrued so far), each month's fee rounded half-up to the cent.
- Dashboard invoice detail page: "Accrued late fees" and "Total with fees" rows under Contract & terms (accrues live off the current outstanding balance).
- New dunning template variables: `{late_fee_due}` and `{total_with_late_fees}` — available in all stages. The shipped `day15.md` formal-notice template now quotes the actual accrued fees and total due.

### Honest scope (late fees)
- A partial month counts as a full month (matches the contract's "compounding monthly" wording). The calculator uses the *current* outstanding balance; it does not retroactively amortize fees day-by-day for payments made mid-accrual — re-run it after recording a payment. Late-fee enforceability varies by jurisdiction.

## [0.3.0] - 2026-10-05

Real e-signature: clients can now sign the contract electronically instead of (or in addition to) acknowledging via the paid deposit link.

### Added
- E-signature capture: `invoiceguard project sign-request <id>` creates a one-time, single-use signing link (`/sign/<token>`, served by the dashboard). The client reads the rendered contract, types their full name, and draws a signature on a canvas (mouse or touch).
- Tamper evidence: at signing time the exact signed contract text is hashed (SHA-256) and stored with the project, together with the signer's name and timestamp — proving *what* was signed, not just *that* something was signed.
- Signing flips `contract_ack` to 1 (a signed contract IS the acknowledgment), with `contract_ack_at` = signing time. The request token is invalidated after use; re-requesting while a request is pending returns the same link.
- `invoiceguard project sign-status <id>` shows pending/signed state, signer, timestamp, and the contract hash. Dashboard signing receipt page shows the captured signature and the hash.

### Changed
- Contract §4 (Sign-off) now describes the electronic signature process instead of the v1 deposit-link acknowledgment. `project ack` (manual acknowledgment) is kept as a fallback path.

### Honest scope (e-signature)
- This is a browser-based typed/drawn signature captured by InvoiceGuard and stored locally — NOT a qualified third-party e-signature service (DocuSign/HelloSign). Legal weight varies by jurisdiction.

## [0.2.0] - 2026-10-04

Partial payments: invoices no longer have to be all-or-nothing.

### Added
- Payment ledger: new `payments` table (additive migration; pre-0.2.0 paid invoices get a labeled backfill row on first open, so old databases keep working)
- `invoiceguard invoice record-payment <id> --amount <dollars> [--note ...] [--method ...]` — record a partial payment; a payment that clears the balance marks the invoice `paid`, otherwise the invoice becomes `partially-paid`
- Dashboard: per-invoice Paid/Outstanding amounts, a payments ledger on the invoice detail page, payment events in the timeline, a new `partially-paid` status filter chip, and totals computed from outstanding balances
- Dunning emails now show paid-so-far and the remaining balance (`{paid}` / `{outstanding}` template variables; the three built-in templates use them)

### Changed
- `invoice mark-paid` now records the full outstanding balance in the ledger (was: status flip with no payment record); it errors instead of silently re-marking an already-paid or void invoice
- Stripe webhooks record the session amount in the ledger (method `stripe`); a session for less than the balance leaves the invoice `partially-paid` instead of `paid`
- Dashboard totals: *Collected* is now the sum of recorded payments, *Outstanding*/*Overdue* are sums of outstanding balances (identical numbers for pre-0.2.0 databases thanks to the backfill)

## [0.1.0] - 2026-10-02

First release on PyPI (`pip install invoiceguard`) and GitHub.

### Added
- CLI: `init`, `client add/list`, `project create/list/ack`, `invoice create/list/mark-paid/void`, `check-due`, `dashboard`
- Stripe deposit payment links per project (test-mode friendly), with `invoiceguard_invoice_id` metadata so webhook payments map back to invoices
- Late-fee contract generator: deposit + late-fee clause markdown, client acknowledgment recorded when the deposit link is accepted
- Automated 3-stage dunning: day-1 polite nudge → day-7 firm reminder → day-15 formal notice quoting the late-fee clause; idempotent, cron-friendly (`check-due`), editable markdown templates
- Localhost web dashboard: invoice list with status filters and escalation stages, per-invoice detail with chronological timeline, totals/outstanding/overdue/collected, Stripe webhook receiver that marks invoices paid
- SQLite persistence (shared schema between CLI and dashboard), Docker Compose setup

### Fixed
- **Dashboard Stripe webhook secret resolution.** The README told users to set `stripe_webhook_secret` in the config (or `INVOICEGUARD_STRIPE_WEBHOOK_SECRET`), but the dashboard only honored the bare `STRIPE_WEBHOOK_SECRET` env var — webhooks returned 500 for anyone following the docs. The endpoint now resolves, in order: `INVOICEGUARD_STRIPE_WEBHOOK_SECRET` env → `stripe_webhook_secret` in the config file → legacy `STRIPE_WEBHOOK_SECRET` env (still supported). Placeholder values (`whsec_...`) count as unconfigured. Regression tests cover all four cases.

### Honest scope (v1)
- The "signature" is a client-acknowledgment checkbox (deposit-link acceptance), not a legal e-signature — real e-signature is on the roadmap.
- Email deliverability is the user's own SMTP; late-fee enforceability varies by jurisdiction.
