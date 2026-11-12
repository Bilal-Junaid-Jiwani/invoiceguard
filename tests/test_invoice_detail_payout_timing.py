"""Agency-mode slice #23: payout timing on the dashboard invoice detail.

Slices #14-#22 put the same per-freelancer rollup on every CLI view
and on the dashboard index's Payouts section — but opening one
invoice still answered only *who did the work*: telling whether that
freelancer needs paying now meant going back to the index or opening
the CLI. This slice puts the timing on the detail page's Freelancer
row itself, taken from the same `payout_schedule_timing()` rollup, as
of today — no new rules. Read-only. An unassigned invoice, or an
assigned invoice whose freelancer has no billable invoices (a draft
does not count), carries no phrase, matching the CLI views.

All dates are relative to today (the dashboard has no --as-of), so
the tests pin nothing to a calendar date: weekly schedules whose pay
weekday is today (due), yesterday (overdue — missed run, payout
predates it), or in two days (upcoming — payout recorded after the
most recent run), plus settled, no-schedule, multi-currency rollup,
unassigned, and draft-assigned cases.
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


def _paid_invoice(db, project, name, split_pct=70.0,
                  paid_cents=90_000, payout_cents=10_000,
                  payout_at=None, cadence="weekly", day=None,
                  currency="USD"):
    """One attributed invoice, `paid_cents` collected and `payout_cents`
    already paid out (on `payout_at`), with a payout schedule. `day`
    is the ISO weekday for weekly schedules. Returns the invoice id.
    """
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid, currency)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, currency,
                         paid_at=payout_at or TEN_DAYS_AGO.isoformat())
    return iid


@pytest.fixture()
def client(db):
    from invoiceguard.web.main import app
    with TestClient(app) as tc:
        yield tc


def test_detail_due_timing(client, db, sample_project):
    iid = _paid_invoice(db, sample_project, "Due Dana",
                        day=TODAY.isoweekday())
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200
    assert "Due Dana" in r.text
    assert f"payout due (next payout {TODAY.isoformat()})" in r.text


def test_detail_overdue_timing(client, db, sample_project):
    # Most recent run was yesterday; the recorded payout (10 days ago)
    # predates it, so the run is still owed.
    iid = _paid_invoice(db, sample_project, "Overdue Omar",
                        day=YESTERDAY.isoweekday())
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200
    assert "Overdue Omar" in r.text
    assert f"payout overdue (missed {YESTERDAY.isoformat()})" in r.text


def test_detail_upcoming_timing(client, db, sample_project):
    # Next run in two days; the most recent run (five days ago) was
    # cleared by a payout recorded yesterday.
    iid = _paid_invoice(db, sample_project, "Upcoming Uma",
                        day=IN_TWO_DAYS.isoweekday(),
                        payout_at=YESTERDAY.isoformat())
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200
    assert "Upcoming Uma" in r.text
    assert (f"payout upcoming (next payout {IN_TWO_DAYS.isoformat()})"
            in r.text)


def test_detail_settled_timing(client, db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share
    # is settled even though the next run is still ahead.
    iid = _paid_invoice(db, sample_project, "Settled Sara",
                        paid_cents=90_000, payout_cents=63_000,
                        day=IN_TWO_DAYS.isoweekday())
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200
    assert "Settled Sara" in r.text
    assert "payout settled" in r.text


def test_detail_no_schedule_timing(client, db, sample_project):
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200
    assert "No Sched" in r.text
    assert "payout no schedule" in r.text


def test_detail_multi_currency_rollup(client, db, sample_project):
    # Overdue in both USD and EUR: one phrase, currency count,
    # earliest missed run.
    fid = db.add_freelancer("Multi Mina", split_pct=70.0,
                            payout_cadence="weekly",
                            payout_day=YESTERDAY.isoweekday())
    iid = None
    for currency in ("USD", "EUR"):
        iid = _invoice(db, sample_project, 100_000, "sent", fid,
                       currency)
        db.record_payment(iid, 90_000, method="bank")
        db.record_payout(fid, 10_000, currency,
                         paid_at=TEN_DAYS_AGO.isoformat())
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200
    assert "Multi Mina" in r.text
    assert (f"payout overdue (2 currencies, missed "
            f"{YESTERDAY.isoformat()})" in r.text)


def test_detail_unassigned_no_phrase(client, db, sample_project):
    iid = _invoice(db, sample_project, 100_000, "sent")
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200
    assert "Freelancer" not in r.text
    assert "payout " not in r.text


def test_detail_draft_assigned_no_phrase(client, db, sample_project):
    # A draft is not billable, so the freelancer has no timing entry
    # even though the invoice is assigned to them.
    fid = db.add_freelancer("Draft Dan", split_pct=70.0,
                            payout_cadence="weekly",
                            payout_day=TODAY.isoweekday())
    iid = _invoice(db, sample_project, 100_000, "draft", fid)
    r = client.get(f"/invoices/{iid}")
    assert r.status_code == 200
    assert "Draft Dan" in r.text
    assert "payout " not in r.text


def test_detail_missing_invoice_still_404(client, db, sample_project):
    r = client.get("/invoices/999999")
    assert r.status_code == 404
