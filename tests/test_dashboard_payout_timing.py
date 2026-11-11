"""Agency-mode slice #22: payout timing on the web dashboard.

The timing series put the same per-freelancer rollup on every CLI
view — agency summary, its TOTALS, `freelancer show`, `payout
suggest`, `payout schedule`, `freelancer list`, the invoice report,
and `payout list` — but the dashboard index still answered only
*what we already sent*: telling whether a freelancer needs paying
now meant opening the CLI. This slice puts the timing on the
dashboard's new Payouts section, taken from the same
`payout_schedule_timing()` rollup, as of today — no new rules.
Read-only. A freelancer with no billable invoices gets no row,
matching the CLI views; an empty roster hides the section.

All dates are relative to today (the dashboard has no --as-of), so
the tests pin nothing to a calendar date: weekly schedules whose
pay weekday is today (due), yesterday (overdue — missed run, payout
predates it), or in two days (upcoming — payout recorded after the
most recent run), plus settled, no-schedule, multi-currency rollup,
no-billable, and empty-roster cases.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
IN_TWO_DAYS = TODAY + timedelta(days=2)
TEN_DAYS_AGO = TODAY - timedelta(days=10)


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _paid_freelancer(db, project, name, split_pct=70.0,
                     paid_cents=90_000, payout_cents=10_000,
                     payout_at=None, cadence="weekly", day=None,
                     currency="USD"):
    """Freelancer with one attributed invoice, `paid_cents` collected
    and `payout_cents` already paid out (on `payout_at`), with a
    payout schedule. `day` is the ISO weekday for weekly schedules.
    """
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid, currency)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, currency,
                         paid_at=payout_at or TEN_DAYS_AGO.isoformat())
    return fid


@pytest.fixture()
def client(db):
    from invoiceguard.web.main import app
    with TestClient(app) as tc:
        yield tc


def test_dashboard_due_timing(client, db, sample_project):
    _paid_freelancer(db, sample_project, "Due Dana",
                     day=TODAY.isoweekday())
    r = client.get("/")
    assert r.status_code == 200
    assert "Due Dana" in r.text
    assert f"payout due (next payout {TODAY.isoformat()})" in r.text


def test_dashboard_overdue_timing(client, db, sample_project):
    # Most recent run was yesterday; the recorded payout (10 days ago)
    # predates it, so the run is still owed.
    _paid_freelancer(db, sample_project, "Overdue Omar",
                     day=YESTERDAY.isoweekday())
    r = client.get("/")
    assert r.status_code == 200
    assert "Overdue Omar" in r.text
    assert f"payout overdue (missed {YESTERDAY.isoformat()})" in r.text


def test_dashboard_upcoming_timing(client, db, sample_project):
    # Next run in two days; the most recent run (five days ago) was
    # cleared by a payout recorded yesterday.
    _paid_freelancer(db, sample_project, "Upcoming Uma",
                     day=IN_TWO_DAYS.isoweekday(),
                     payout_at=YESTERDAY.isoformat())
    r = client.get("/")
    assert r.status_code == 200
    assert "Upcoming Uma" in r.text
    assert (f"payout upcoming (next payout {IN_TWO_DAYS.isoformat()})"
            in r.text)


def test_dashboard_settled_timing(client, db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share
    # is settled even though the next run is still ahead.
    _paid_freelancer(db, sample_project, "Settled Sara",
                     paid_cents=90_000, payout_cents=63_000,
                     day=IN_TWO_DAYS.isoweekday())
    r = client.get("/")
    assert r.status_code == 200
    assert "Settled Sara" in r.text
    assert "payout settled" in r.text


def test_dashboard_no_schedule_timing(client, db, sample_project):
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    r = client.get("/")
    assert r.status_code == 200
    assert "No Sched" in r.text
    assert "payout no schedule" in r.text


def test_dashboard_multi_currency_rollup(client, db, sample_project):
    # Overdue in both USD and EUR: one line, currency count, earliest
    # missed run.
    fid = db.add_freelancer("Multi Mina", split_pct=70.0,
                            payout_cadence="weekly",
                            payout_day=YESTERDAY.isoweekday())
    for currency in ("USD", "EUR"):
        iid = _invoice(db, sample_project, 100_000, "sent", fid,
                       currency)
        db.record_payment(iid, 90_000, method="bank")
        db.record_payout(fid, 10_000, currency,
                         paid_at=TEN_DAYS_AGO.isoformat())
    r = client.get("/")
    assert r.status_code == 200
    assert "Multi Mina" in r.text
    assert (f"payout overdue (2 currencies, missed "
            f"{YESTERDAY.isoformat()})" in r.text)


def test_dashboard_no_billable_no_row(client, db, sample_project):
    # Schedule set, but no invoices at all: no row, matching the CLI
    # views — and no misleading phrase.
    db.add_freelancer("No Billable", split_pct=70.0,
                      payout_cadence="weekly",
                      payout_day=TODAY.isoweekday())
    r = client.get("/")
    assert r.status_code == 200
    assert "No Billable" not in r.text
    assert "No payout timing yet" in r.text


def test_dashboard_empty_roster_no_section(client, db, sample_project):
    # No freelancers on the roster: the whole Payouts section hides
    # rather than rendering an empty box.
    r = client.get("/")
    assert r.status_code == 200
    assert "Payouts" not in r.text
