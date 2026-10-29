"""Agency-mode slice #10: payouts due on the pay run.

Slices #6 and #9 answered "what do I pay?" and "when do I pay?"
separately; this slice joins them — `reports.payout_due()` plus
`payout due`: a scheduled freelancer whose next payout date falls
on (or within --within-days after) the pay-run date AND who still
has an actionable payout suggestion. Read-only: listing the pay
run never records it.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import payout_due


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _split_freelancer(db, project, name="Ali R.", split_pct=70.0,
                      paid_cents=90_000, payout_cents=25_000,
                      cadence="weekly", day=3):
    """Freelancer with one attributed invoice, `paid_cents` collected,
    `payout_cents` already paid out, and a payout schedule."""
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, "USD")
    return fid


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_due_today_with_split_share(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # weekly Wed, due today
    rows = payout_due(db, as_of="2026-10-28")
    assert len(rows) == 1
    r = rows[0]
    assert r["freelancer_id"] == fid
    assert r["next_payout_date"] == "2026-10-28"
    assert r["payout_cadence"] == "weekly"
    assert r["suggested_cents"] == 38_000  # 63k share minus 25k paid


def test_not_yet_due_excluded_until_within_days(db, sample_project):
    # Weekly Friday: next payout 2026-10-30, not due on the 28th.
    fid = _split_freelancer(db, sample_project, cadence="weekly", day=5)
    assert payout_due(db, as_of="2026-10-28") == []
    rows = payout_due(db, as_of="2026-10-28", within_days=7)
    assert [r["freelancer_id"] for r in rows] == [fid]
    assert rows[0]["next_payout_date"] == "2026-10-30"


def test_unscheduled_freelancer_never_due(db, sample_project):
    fid = db.add_freelancer("Sana K.", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    assert payout_due(db, as_of="2026-10-28") == []


def test_settled_freelancer_with_due_date_produces_no_row(
        db, sample_project):
    # Due today (weekly Wed) but the full share is already paid.
    _split_freelancer(db, sample_project, payout_cents=63_000)
    assert payout_due(db, as_of="2026-10-28") == []


def test_no_split_due_row_reports_available(db, sample_project):
    fid = db.add_freelancer("Sana K.", payout_cadence="monthly",
                            payout_day=28)  # due 2026-10-28
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD")
    rows = payout_due(db, as_of="2026-10-28")
    assert len(rows) == 1
    assert rows[0]["suggested_cents"] is None
    assert rows[0]["available_cents"] == 80_000


def test_rows_sorted_earliest_date_then_largest_amount(
        db, sample_project):
    # Both due 2026-10-28 (weekly Wed): larger share first.
    big = _split_freelancer(db, sample_project, name="Ali R.",
                            split_pct=80.0, payout_cents=0)
    small = _split_freelancer(db, sample_project, name="Zed Q.",
                              split_pct=50.0, paid_cents=40_000,
                              payout_cents=0)
    # Due later (monthly day 15 -> 2026-11-15), within 30 days.
    late = _split_freelancer(db, sample_project, name="Mia L.",
                             cadence="monthly", day=15, payout_cents=0)
    rows = payout_due(db, as_of="2026-10-28", within_days=30)
    assert [r["freelancer_id"] for r in rows] == [big, small, late]
    assert rows[0]["suggested_cents"] == 72_000
    assert rows[1]["suggested_cents"] == 20_000


def test_single_freelancer_unknown_and_negative_window(db):
    with pytest.raises(ValueError, match="no freelancer"):
        payout_due(db, 999, as_of="2026-10-28")
    with pytest.raises(ValueError, match="within_days"):
        payout_due(db, as_of="2026-10-28", within_days=-1)


def test_cli_due(db, sample_project):
    _split_freelancer(db, sample_project)
    _split_freelancer(db, sample_project, name="Sana K.",
                      cadence="weekly", day=5)  # Fri, not due today
    out = CliRunner().invoke(cli, ["payout", "due",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "380.00" in out.output
    assert "due 2026-10-28" in out.output
    assert "Sana K." not in out.output
    out = CliRunner().invoke(cli, ["payout", "due",
                                   "--as-of", "2026-10-28",
                                   "--within-days", "7"])
    assert out.exit_code == 0, out.output
    assert "Sana K." in out.output
    assert "due 2026-10-30" in out.output


def test_cli_due_empty_and_unknown(db, sample_project):
    out = CliRunner().invoke(cli, ["payout", "due"])
    assert out.exit_code == 0
    assert "no freelancers on the roster" in out.output
    _split_freelancer(db, sample_project, cadence="weekly", day=5)
    out = CliRunner().invoke(cli, ["payout", "due",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0
    assert "no payouts due" in out.output
    out = CliRunner().invoke(cli, ["payout", "due", "--freelancer", "999"])
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output
    out = CliRunner().invoke(cli, ["payout", "due", "--within-days", "-1"])
    assert out.exit_code != 0
    assert "within_days" in out.output
