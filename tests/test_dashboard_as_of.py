"""Agency-mode slice #24: dashboard payout timing as of a projected date.

Slices #14–#23 put the same `payout_schedule_timing()` rollup on
every CLI view and on both dashboard pages — but the dashboard was
stuck on today: the CLI's `--as-of` had no web equivalent, so
planning next week's pay run ("who will be due on Friday?") meant
opening the CLI. This slice adds `?as_of=YYYY-MM-DD` to the index
and the invoice detail page, projecting the Payouts section and the
detail page's Freelancer row to that day — same rollup, same rules,
no new library code. The invoice list, totals, and escalation stages
stay as-of-today: only the payout timing is projected, and the page
says so. An invalid date is a clean 400, not a traceback; the
status-filter and Details links keep the projection, and the header
links back to today. Read-only.

All dates are relative to today, so the tests pin nothing to a
calendar date: a weekly schedule whose pay weekday is in two days
(upcoming today, due on that day), a weekly schedule whose pay
weekday is today (due today, overdue the day after), plus invalid
dates, link preservation, and the unprojected default.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)
TOMORROW = TODAY + timedelta(days=1)
IN_TWO_DAYS = TODAY + timedelta(days=2)
TEN_DAYS_AGO = TODAY - timedelta(days=10)


def _paid_freelancer(db, project, name, split_pct=70.0,
                     paid_cents=90_000, payout_cents=10_000,
                     payout_at=None, cadence="weekly", day=None,
                     currency="USD"):
    """Freelancer with one attributed invoice, `paid_cents` collected
    and `payout_cents` already paid out (on `payout_at`), with a
    payout schedule. `day` is the ISO weekday for weekly schedules.
    Returns (freelancer id, invoice id).
    """
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = db.add_invoice(project["id"], "final", 100_000, currency,
                         status="sent", due_date="2026-01-01")
    db.assign_invoice_freelancer(iid, fid)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, currency,
                         paid_at=payout_at or TEN_DAYS_AGO.isoformat())
    return fid, iid


def test_index_as_of_projects_to_due(db, sample_project):
    # Next run in two days; the most recent run (five days ago) was
    # cleared by a payout recorded yesterday — upcoming as of today,
    # due as of the run day itself.
    _paid_freelancer(db, sample_project, "Projected Priya",
                     day=IN_TWO_DAYS.isoweekday(),
                     payout_at=YESTERDAY.isoformat())
    from invoiceguard.web.main import app
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "payout upcoming (next payout " in r.text
        assert "payout timing as of today" in r.text

        r = client.get(f"/?as_of={IN_TWO_DAYS.isoformat()}")
        assert r.status_code == 200
        assert "Projected Priya" in r.text
        assert (f"payout due (next payout {IN_TWO_DAYS.isoformat()})"
                in r.text)
        assert (f"payout timing as of {IN_TWO_DAYS.isoformat()}"
                in r.text)
        assert "back to today" in r.text


def test_index_as_of_projects_to_overdue(db, sample_project):
    # Pay weekday is today; the recorded payout (10 days ago) predates
    # today's run, so as of tomorrow that run is missed and still owed.
    _paid_freelancer(db, sample_project, "Late Leo",
                     day=TODAY.isoweekday())
    from invoiceguard.web.main import app
    with TestClient(app) as client:
        r = client.get(f"/?as_of={TOMORROW.isoformat()}")
        assert r.status_code == 200
        assert "Late Leo" in r.text
        assert f"payout overdue (missed {TODAY.isoformat()})" in r.text


def test_index_invalid_as_of_is_400(db, sample_project):
    from invoiceguard.web.main import app
    with TestClient(app) as client:
        for bad in ("not-a-date", "2026-13-40", "2026-02-30"):
            r = client.get(f"/?as_of={bad}")
            assert r.status_code == 400
            assert "YYYY-MM-DD" in r.text


def test_detail_as_of_projects_timing(db, sample_project):
    _, iid = _paid_freelancer(db, sample_project, "Detail Dana",
                              day=IN_TWO_DAYS.isoweekday(),
                              payout_at=YESTERDAY.isoformat())
    from invoiceguard.web.main import app
    with TestClient(app) as client:
        r = client.get(f"/invoices/{iid}?as_of={IN_TWO_DAYS.isoformat()}")
        assert r.status_code == 200
        assert (f"payout due (next payout {IN_TWO_DAYS.isoformat()})"
                in r.text)
        assert f"(as of {IN_TWO_DAYS.isoformat()})" in r.text
        # The back link keeps the projection.
        assert f'href="/?as_of={IN_TWO_DAYS.isoformat()}"' in r.text


def test_detail_invalid_as_of_is_400(db, sample_project):
    _, iid = _paid_freelancer(db, sample_project, "Detail Dev",
                              day=IN_TWO_DAYS.isoweekday(),
                              payout_at=YESTERDAY.isoformat())
    from invoiceguard.web.main import app
    with TestClient(app) as client:
        r = client.get(f"/invoices/{iid}?as_of=junk")
        assert r.status_code == 400
        assert "YYYY-MM-DD" in r.text
        # And a missing invoice is still a 404, projection or not.
        r = client.get(f"/invoices/999999?as_of={TODAY.isoformat()}")
        assert r.status_code == 404


def test_index_links_keep_projection(db, sample_project):
    _paid_freelancer(db, sample_project, "Linked Lina",
                     day=IN_TWO_DAYS.isoweekday(),
                     payout_at=YESTERDAY.isoformat())
    from invoiceguard.web.main import app
    with TestClient(app) as client:
        r = client.get(f"/?as_of={IN_TWO_DAYS.isoformat()}")
        assert r.status_code == 200
        # Status filters and the All link carry the projection.
        assert (f"status=sent&as_of={IN_TWO_DAYS.isoformat()}"
                in r.text)
        assert f'href="/?as_of={IN_TWO_DAYS.isoformat()}"' in r.text
        # The back-to-today link drops it (keeping no filter here).
        assert 'href="/"' in r.text


def test_index_no_projection_unchanged(db, sample_project):
    _paid_freelancer(db, sample_project, "Plain Pia",
                     day=IN_TWO_DAYS.isoweekday(),
                     payout_at=YESTERDAY.isoformat())
    from invoiceguard.web.main import app
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "payout timing as of today" in r.text
        assert "back to today" not in r.text
        assert "as_of=" not in r.text
