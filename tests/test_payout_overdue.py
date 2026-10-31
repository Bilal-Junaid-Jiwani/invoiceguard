"""Agency-mode slice #12: payouts overdue from a missed pay run.

`payout due` only looks forward, so the morning after a missed pay
run it reports "no payouts due" even though money is still owed past
a pay date. `reports.payout_overdue()` plus `payout overdue` look
backward instead: a scheduled freelancer whose most recent scheduled
payout date has passed, who still has an actionable suggestion, and
who has nothing recorded for that currency since that missed date.
Due-today freelancers stay in `payout due`, and money collected since
the last run waits for the next run. Read-only: listing the missed
run never records it.
"""

from __future__ import annotations

from datetime import date

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import payout_overdue, previous_payout_date


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _split_freelancer(db, project, name="Ali R.", split_pct=70.0,
                      paid_cents=90_000, payout_cents=0,
                      payout_paid_at=None, cadence="weekly", day=3):
    """Freelancer with one attributed invoice, `paid_cents` collected,
    `payout_cents` already paid out (on `payout_paid_at` when given),
    and a payout schedule."""
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, "USD",
                         paid_at=payout_paid_at)
    return fid


def test_monthly_missed_run(db, sample_project):
    # Monthly day 15, as of 2026-10-31: next run Nov 15, missed Oct 15.
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)
    rows = payout_overdue(db, as_of="2026-10-31")
    assert len(rows) == 1
    r = rows[0]
    assert r["freelancer_id"] == fid
    assert r["missed_payout_date"] == "2026-10-15"
    assert r["next_payout_date"] == "2026-11-15"
    assert r["payout_cadence"] == "monthly"
    assert r["suggested_cents"] == 63_000  # 70% of 90k collected


def test_weekly_missed_run(db, sample_project):
    # Weekly Friday, as of Wed 2026-10-28: next Oct 30, missed Oct 23.
    fid = _split_freelancer(db, sample_project, cadence="weekly", day=5)
    rows = payout_overdue(db, as_of="2026-10-28")
    assert [r["freelancer_id"] for r in rows] == [fid]
    assert rows[0]["missed_payout_date"] == "2026-10-23"
    assert rows[0]["next_payout_date"] == "2026-10-30"


def test_due_today_is_not_overdue(db, sample_project):
    # Weekly Wed, due 2026-10-28 itself: in `payout due`, never here.
    _split_freelancer(db, sample_project)  # weekly Wed, due today
    assert payout_overdue(db, as_of="2026-10-28") == []


def test_paid_at_missed_run_not_overdue(db, sample_project):
    # Paid 25k on the missed Oct 15 run; 38k still due, but the run
    # was covered, so the remainder waits for the next run.
    _split_freelancer(db, sample_project, cadence="monthly", day=15,
                      payout_cents=25_000, payout_paid_at="2026-10-15")
    assert payout_overdue(db, as_of="2026-10-31") == []


def test_paid_after_missed_run_not_overdue(db, sample_project):
    # Paid 25k on Oct 20, after the missed Oct 15 run: money collected
    # since the last run waits for the next run, it is not a missed one.
    _split_freelancer(db, sample_project, cadence="monthly", day=15,
                      payout_cents=25_000, payout_paid_at="2026-10-20")
    assert payout_overdue(db, as_of="2026-10-31") == []


def test_paid_before_missed_run_still_overdue(db, sample_project):
    # Last payout was the Sep 15 run; nothing at/after Oct 15, so the
    # Oct 15 run was missed even though earlier runs were paid.
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15, payout_cents=25_000,
                            payout_paid_at="2026-09-15")
    rows = payout_overdue(db, as_of="2026-10-31")
    assert [r["freelancer_id"] for r in rows] == [fid]
    assert rows[0]["missed_payout_date"] == "2026-10-15"
    assert rows[0]["suggested_cents"] == 38_000


def test_unscheduled_and_settled_never_overdue(db, sample_project):
    fid = db.add_freelancer("Sana K.", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    assert payout_overdue(db, as_of="2026-10-31") == []
    # Due date passed but the full share is already paid.
    _split_freelancer(db, sample_project, name="Zed Q.",
                      cadence="monthly", day=15, payout_cents=63_000,
                      payout_paid_at="2026-10-15")
    assert payout_overdue(db, as_of="2026-10-31") == []


def test_no_split_overdue_row_reports_available(db, sample_project):
    fid = db.add_freelancer("Sana K.", payout_cadence="monthly",
                            payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-09-15")
    rows = payout_overdue(db, as_of="2026-10-31")
    assert len(rows) == 1
    assert rows[0]["suggested_cents"] is None
    assert rows[0]["available_cents"] == 80_000


def test_rows_sorted_earliest_missed_then_largest_amount(
        db, sample_project):
    # Same missed date (monthly day 15): larger share first.
    big = _split_freelancer(db, sample_project, name="Ali R.",
                            split_pct=80.0, cadence="monthly", day=15)
    small = _split_freelancer(db, sample_project, name="Zed Q.",
                              split_pct=50.0, paid_cents=40_000,
                              cadence="monthly", day=15)
    # Later missed date (weekly Friday, missed Oct 23) sorts last.
    late = _split_freelancer(db, sample_project, name="Mia L.",
                             cadence="weekly", day=5)
    rows = payout_overdue(db, as_of="2026-10-31")
    assert [r["freelancer_id"] for r in rows] == [big, small, late]
    assert rows[0]["suggested_cents"] == 72_000
    assert rows[1]["suggested_cents"] == 20_000


def test_previous_payout_date_weekly_monthly_and_clamp():
    assert previous_payout_date("weekly", 3,
                                as_of="2026-10-28") == date(2026, 10, 21)
    assert previous_payout_date("monthly", 15,
                                as_of="2026-10-31") == date(2026, 10, 15)
    # Day-31 schedule: March's previous date is Feb 28 (2026, not leap).
    assert previous_payout_date("monthly", 31,
                                as_of="2026-03-05") == date(2026, 2, 28)
    assert previous_payout_date(None, None) is None


def test_single_freelancer_unknown(db, sample_project):
    with pytest.raises(ValueError, match="no freelancer"):
        payout_overdue(db, 999, as_of="2026-10-31")


def test_cli_overdue(db, sample_project):
    _split_freelancer(db, sample_project, cadence="monthly", day=15)
    _split_freelancer(db, sample_project, name="Sana K.",
                      cadence="weekly", day=5)  # missed Oct 30
    # Due 2026-10-31 itself (weekly Saturday): due, not overdue.
    _split_freelancer(db, sample_project, name="Omar T.",
                      cadence="weekly", day=6)
    out = CliRunner().invoke(cli, ["payout", "overdue",
                                   "--as-of", "2026-10-31"])
    assert out.exit_code == 0, out.output
    assert "630.00" in out.output
    assert "missed 2026-10-15" in out.output
    assert "missed 2026-10-30" in out.output
    assert "next payout 2026-11-15" in out.output
    assert "Omar T." not in out.output


def test_cli_overdue_empty_and_unknown(db, sample_project):
    out = CliRunner().invoke(cli, ["payout", "overdue"])
    assert out.exit_code == 0
    assert "no freelancers on the roster" in out.output
    _split_freelancer(db, sample_project, cadence="weekly", day=6)
    out = CliRunner().invoke(cli, ["payout", "overdue",
                                   "--as-of", "2026-10-31"])
    assert out.exit_code == 0
    assert "no overdue payouts" in out.output
    out = CliRunner().invoke(cli, ["payout", "overdue",
                                   "--freelancer", "999"])
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output
