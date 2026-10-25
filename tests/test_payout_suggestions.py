"""Agency-mode slice #6: payout suggestions.

Slice #5 stored a per-freelancer revenue split and reported the
still-due share; slice #6 turns that into an answer to "what do I pay
next?" — `reports.payout_suggestions()` plus `payout suggest`. The
suggestion is derived and read-only: nothing is recorded or
transferred, and `payout record` still enforces its own guard.
"""

from __future__ import annotations

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import payout_suggestions


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _split_freelancer(db, project, split_pct=70.0, paid_cents=90_000,
                      currency="USD"):
    """Freelancer with one attributed invoice and `paid_cents` collected."""
    fid = db.add_freelancer("Ali R.", split_pct=split_pct)
    iid = _invoice(db, project, 100_000, "sent", fid, currency=currency)
    db.record_payment(iid, paid_cents, method="bank")
    return fid


def test_suggests_still_due_share(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # 90k collected, 70% split
    db.record_payout(fid, 25_000, "USD")
    rows = payout_suggestions(db, fid)
    assert len(rows) == 1
    r = rows[0]
    assert r["freelancer_id"] == fid
    assert r["currency"] == "USD"
    assert r["collected_cents"] == 90_000
    assert r["expected_payout_cents"] == 63_000
    assert r["paid_out_cents"] == 25_000
    assert r["suggested_cents"] == 38_000  # 63k share minus 25k paid


def test_settled_freelancer_gets_no_suggestion(db, sample_project):
    fid = _split_freelancer(db, sample_project)
    db.record_payout(fid, 63_000, "USD")  # full share paid
    assert payout_suggestions(db, fid) == []


def test_paid_past_share_gets_no_suggestion(db, sample_project):
    # The payout guard allows paying past the split share, up to what
    # was collected — and the suggestion must not then go negative.
    fid = _split_freelancer(db, sample_project)
    db.record_payout(fid, 70_000, "USD")  # past the 63k share, within 90k
    assert payout_suggestions(db, fid) == []


def test_no_split_reports_available_not_a_share(db, sample_project):
    fid = db.add_freelancer("Sana K.")
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD")
    rows = payout_suggestions(db, fid)
    assert len(rows) == 1
    assert rows[0]["suggested_cents"] is None
    assert rows[0]["available_cents"] == 80_000


def test_no_billable_invoices_no_suggestion(db, sample_project):
    fid = db.add_freelancer("No Work Yet", split_pct=70.0)
    assert payout_suggestions(db, fid) == []


def test_nothing_collected_no_suggestion(db, sample_project):
    fid = db.add_freelancer("Unpaid Work", split_pct=70.0)
    _invoice(db, sample_project, 100_000, "sent", fid)  # no payments
    assert payout_suggestions(db, fid) == []


def test_unknown_freelancer_raises(db):
    import pytest
    with pytest.raises(ValueError, match="no freelancer"):
        payout_suggestions(db, 999)


def test_suggestions_sorted_largest_first(db, sample_project):
    big = _split_freelancer(db, sample_project, split_pct=80.0)
    # second freelancer, smaller share: rename via a fresh roster entry
    small = db.add_freelancer("Zed Q.", split_pct=50.0)
    iid = _invoice(db, sample_project, 100_000, "sent", small)
    db.record_payment(iid, 40_000, method="bank")
    rows = payout_suggestions(db)
    assert [r["freelancer_id"] for r in rows] == [big, small]
    assert rows[0]["suggested_cents"] == 72_000
    assert rows[1]["suggested_cents"] == 20_000


def test_cli_suggest(db, sample_project):
    fid = _split_freelancer(db, sample_project)
    db.record_payout(fid, 25_000, "USD")
    out = CliRunner().invoke(cli, ["payout", "suggest"])
    assert out.exit_code == 0, out.output
    assert "380.00" in out.output
    assert "70" in out.output and "collected" in out.output


def test_cli_suggest_no_suggestions_and_unknown(db, sample_project):
    out = CliRunner().invoke(cli, ["payout", "suggest"])
    assert out.exit_code == 0
    assert "no payout suggestions" in out.output
    out = CliRunner().invoke(cli, ["payout", "suggest",
                                   "--freelancer", "999"])
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output


def test_cli_suggest_filter(db, sample_project):
    _split_freelancer(db, sample_project)  # Ali R., 70% of 90k = 630
    other = db.add_freelancer("Other O.", split_pct=50.0)
    iid = _invoice(db, sample_project, 100_000, "sent", other)
    db.record_payment(iid, 40_000, method="bank")
    out = CliRunner().invoke(cli, ["payout", "suggest",
                                   "--freelancer", str(other)])
    assert out.exit_code == 0, out.output
    assert "200.00" in out.output
    assert "Ali R." not in out.output
