"""Agency-mode slice #4: freelancer payout tracking (what the agency paid out).

Slice #3 answered what each freelancer's attributed work billed and what
the payment ledger collected. Slice #4 closes the other side of the
bookkeeping: a payout ledger recording what the agency has actually sent
each freelancer, per currency. Bookkeeping only — nothing is transferred,
and payouts are never derived from earnings automatically.

The hard rule: a payout is refused when it exceeds what the agency has
collected for that freelancer in that currency (collected minus payouts
already recorded) — you cannot pay out money you have not received.
"""

from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.db import DB
from invoiceguard.reports import freelancer_earnings


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD", kind="final"):
    iid = db.add_invoice(project["id"], kind, cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _collected_freelancer(db, project, paid_cents=90_000, currency="USD"):
    """Freelancer with one attributed invoice and `paid_cents` collected."""
    fid = db.add_freelancer("Ali R.")
    iid = _invoice(db, project, 100_000, "sent", fid, currency=currency)
    db.record_payment(iid, paid_cents, method="bank")
    return fid


def test_record_payout_basic(db, sample_project):
    fid = _collected_freelancer(db, sample_project)  # 90k USD collected
    rec = db.record_payout(fid, 25_000, "USD", method="bank",
                           note="September share")
    assert rec["id"] is not None
    assert rec["currency"] == "USD"
    assert db.paid_out_cents(fid, "USD") == 25_000
    # paid_at defaults to now
    assert rec["paid_at"]


def test_payout_refused_beyond_collected(db, sample_project):
    fid = _collected_freelancer(db, sample_project)  # 90k collected
    with pytest.raises(ValueError, match="exceeds"):
        db.record_payout(fid, 90_001, "USD")
    # exact boundary is fine
    db.record_payout(fid, 90_000, "USD")
    assert db.paid_out_cents(fid, "USD") == 90_000
    # and a second payout is now refused — nothing collected remains unpaid
    with pytest.raises(ValueError, match="exceeds"):
        db.record_payout(fid, 1, "USD")


def test_payout_per_currency_guard(db, sample_project):
    fid = db.add_freelancer("Sana K.")
    _invoice(db, sample_project, 100_000, "sent", fid, currency="EUR")
    # no USD collected for her, so a USD payout is refused...
    with pytest.raises(ValueError, match="exceeds"):
        db.record_payout(fid, 10_000, "USD")
    # ...and a EUR payout is refused too (no payments recorded yet)
    with pytest.raises(ValueError, match="exceeds"):
        db.record_payout(fid, 10_000, "EUR")


def test_payout_currency_case_insensitive(db, sample_project):
    fid = db.add_freelancer("Ali R.")
    iid = _invoice(db, sample_project, 100_000, "sent", fid, currency="usd")
    db.record_payment(iid, 50_000, method="bank")
    # payout currency normalizes to upper; collected matches regardless
    rec = db.record_payout(fid, 10_000, "usd")
    assert rec["currency"] == "USD"
    assert db.paid_out_cents(fid, "USD") == 10_000


def test_record_payout_validation(db, sample_project):
    fid = _collected_freelancer(db, sample_project)
    with pytest.raises(ValueError, match="no freelancer"):
        db.record_payout(999, 10_000)
    with pytest.raises(ValueError, match="positive"):
        db.record_payout(fid, 0)
    with pytest.raises(ValueError, match="invalid date"):
        db.record_payout(fid, 1_000, paid_at="not-a-date")
    # partial ISO date is accepted and stored as a full timestamp
    rec = db.record_payout(fid, 1_000, paid_at="2026-09-30")
    assert rec["paid_at"].startswith("2026-09-30")


def test_payouts_table_added_to_old_db(home, tmp_path):
    """Databases created before v0.10.0 get the payouts table on connect."""
    old_db_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(old_db_path))
    conn.execute("CREATE TABLE freelancers(id INTEGER PRIMARY KEY, "
                 "name TEXT, email TEXT, phone TEXT, created_at TEXT)")
    conn.commit()
    conn.close()
    d = DB(old_db_path)
    fid = d.add_freelancer("Ali R.")
    rows = d.conn.execute(
        "PRAGMA table_info(payouts)").fetchall()
    assert any(r["name"] == "amount_cents" for r in rows)
    d.close()


def test_list_payouts_newest_first(db, sample_project):
    fid = _collected_freelancer(db, sample_project)  # 90k collected
    db.record_payout(fid, 10_000, paid_at="2026-09-01", method="bank")
    db.record_payout(fid, 20_000, paid_at="2026-10-01", method="bank")
    rows = db.list_payouts()
    assert [r["amount_cents"] for r in rows] == [20_000, 10_000]
    assert rows[0]["freelancer_name"] == "Ali R."
    assert db.list_payouts(freelancer_id=999) == []
    assert len(db.list_payouts(freelancer_id=fid)) == 2


def test_earnings_rows_carry_payout_totals(db, sample_project):
    fid = _collected_freelancer(db, sample_project)  # 90k collected
    db.record_payout(fid, 25_000, "USD")
    rows = freelancer_earnings(db, fid)
    assert len(rows) == 1
    r = rows[0]
    assert r["paid_out_cents"] == 25_000
    assert r["still_owed_cents"] == 90_000 - 25_000


def test_earnings_no_payouts_still_owed_equals_collected(db, sample_project):
    fid = _collected_freelancer(db, sample_project)
    r = freelancer_earnings(db, fid)[0]
    assert r["paid_out_cents"] == 0
    assert r["still_owed_cents"] == r["collected_cents"]


def test_cli_payout_roundtrip(db, sample_project):
    fid = _collected_freelancer(db, sample_project)  # 90k collected
    runner = CliRunner()
    r = runner.invoke(cli, ["payout", "record", "--freelancer", str(fid),
                            "--amount", "250", "--currency", "USD",
                            "--method", "bank", "--note", "Sept share"])
    assert r.exit_code == 0, r.output
    assert "freelancer #1" in r.output and "250.00" in r.output

    r = runner.invoke(cli, ["payout", "list"])
    assert r.exit_code == 0, r.output
    assert "Ali R." in r.output and "250.00" in r.output

    r = runner.invoke(cli, ["payout", "list", "--freelancer", str(fid)])
    assert r.exit_code == 0, r.output
    assert "250.00" in r.output

    # over-payout is a clean CLI error, not a traceback
    r = runner.invoke(cli, ["payout", "record", "--freelancer", str(fid),
                            "--amount", "1000000"])
    assert r.exit_code != 0
    assert "exceeds" in r.output

    # unknown freelancer is a clean CLI error
    r = runner.invoke(cli, ["payout", "record", "--freelancer", "999",
                            "--amount", "10"])
    assert r.exit_code != 0
    assert "no freelancer #999" in r.output


def test_cli_freelancer_show_shows_payouts(db, sample_project):
    fid = _collected_freelancer(db, sample_project)  # 90k collected
    db.record_payout(fid, 25_000, "USD", paid_at="2026-09-30",
                     method="bank", note="Sept share")
    runner = CliRunner()
    r = runner.invoke(cli, ["freelancer", "show", str(fid)])
    assert r.exit_code == 0, r.output
    assert "paid out 250.00" in r.output
    assert "still owed 650.00" in r.output
    assert "payouts:" in r.output
    assert "Sept share" in r.output
