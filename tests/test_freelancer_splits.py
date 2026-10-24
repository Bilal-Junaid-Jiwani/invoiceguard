"""Agency-mode slice #5: per-freelancer revenue-split percentage.

Slice #4 recorded what the agency paid each freelancer, guarded by what
was collected. Slice #5 answers the question that sits between the two:
how much of the collected money is the freelancer's share? A revenue
split (e.g. 70%) is stored on the roster freelancer; `freelancer show`
and `reports.freelancer_earnings()` then report the freelancer's
expected share of each collected currency and what is still due to them
after recorded payouts. Bookkeeping only — the payout guard still
refuses any payout larger than what was collected, and a split never
moves money on its own.
"""

from __future__ import annotations

import os
import sqlite3

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.db import DB, migrate_freelancers_split_pct
from invoiceguard.reports import freelancer_earnings


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD", kind="final"):
    iid = db.add_invoice(project["id"], kind, cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _collected_freelancer(db, project, paid_cents=90_000, currency="USD",
                          split_pct=None):
    """Freelancer with one attributed invoice and `paid_cents` collected."""
    fid = db.add_freelancer("Ali R.", split_pct=split_pct)
    iid = _invoice(db, project, 100_000, "sent", fid, currency=currency)
    db.record_payment(iid, paid_cents, method="bank")
    return fid


# -- DB-level ----------------------------------------------------------

def test_add_freelancer_with_split(db):
    fid = db.add_freelancer("Ali R.", split_pct=70.0)
    assert db.get_freelancer(fid)["split_pct"] == 70.0


def test_add_freelancer_without_split_stays_null(db):
    fid = db.add_freelancer("Ali R.")
    assert db.get_freelancer(fid)["split_pct"] is None


@pytest.mark.parametrize("bad", [-1.0, 100.5, 500.0])
def test_add_freelancer_rejects_bad_split(db, bad):
    with pytest.raises(ValueError, match="between 0 and 100"):
        db.add_freelancer("Ali R.", split_pct=bad)


def test_set_freelancer_split(db):
    fid = db.add_freelancer("Ali R.")
    db.set_freelancer_split(fid, 65.5)
    assert db.get_freelancer(fid)["split_pct"] == 65.5


def test_set_freelancer_split_unknown_and_bad(db):
    with pytest.raises(ValueError, match="no freelancer #999"):
        db.set_freelancer_split(999, 50.0)
    fid = db.add_freelancer("Ali R.")
    with pytest.raises(ValueError, match="between 0 and 100"):
        db.set_freelancer_split(fid, 101.0)
    # failed write leaves the old value in place
    assert db.get_freelancer(fid)["split_pct"] is None


def test_migration_adds_split_pct_to_old_db(home):
    """A pre-v0.11.0 database gets freelancers.split_pct on connect."""
    path = os.environ["INVOICEGUARD_DB"]
    conn = sqlite3.connect(path)
    # A faithful pre-v0.11.0 freelancers table: every column EXCEPT
    # split_pct.
    conn.execute(
        "CREATE TABLE freelancers(id INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL, email TEXT, phone TEXT, "
        "created_at TEXT NOT NULL)")
    conn.commit()
    migrate_freelancers_split_pct(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(freelancers)")}
    conn.close()
    assert "split_pct" in cols
    # idempotent: reopening via DB does not lose data
    d = DB(str(path))
    fid = d.add_freelancer("Ali R.")
    assert d.get_freelancer(fid)["split_pct"] is None
    d.close()


# -- earnings rows -----------------------------------------------------

def test_earnings_rows_carry_split_share(db, sample_project):
    fid = _collected_freelancer(db, sample_project, split_pct=70.0)
    (row,) = freelancer_earnings(db, fid)
    assert row["split_pct"] == 70.0
    assert row["collected_cents"] == 90_000
    assert row["expected_payout_cents"] == 63_000
    assert row["still_due_to_freelancer_cents"] == 63_000
    db.record_payout(fid, 20_000, "USD")
    (row,) = freelancer_earnings(db, fid)
    assert row["still_due_to_freelancer_cents"] == 43_000


def test_earnings_rows_split_is_none_without_split(db, sample_project):
    fid = _collected_freelancer(db, sample_project)
    (row,) = freelancer_earnings(db, fid)
    assert row["split_pct"] is None
    assert row["expected_payout_cents"] is None
    assert row["still_due_to_freelancer_cents"] is None


def test_earnings_split_rounds_half_up(db, sample_project):
    # 33.33% of 3_333 cents = 1110.98889 -> 1111 (half-up to the cent)
    fid = _collected_freelancer(db, sample_project, paid_cents=3_333,
                                split_pct=33.33)
    (row,) = freelancer_earnings(db, fid)
    assert row["expected_payout_cents"] == 1_111


def test_earnings_split_applies_per_currency(db, sample_project):
    fid = db.add_freelancer("Ali R.", split_pct=50.0)
    _invoice(db, sample_project, 100_000, "sent", fid, currency="USD")
    iid = db.add_invoice(sample_project["id"], "final", 200_000, "EUR",
                         status="sent", due_date="2026-01-01")
    db.assign_invoice_freelancer(iid, fid)
    for inv_id in db.conn.execute(
            "SELECT id FROM invoices WHERE freelancer_id = ?", (fid,)):
        db.record_payment(inv_id[0], 45_000, method="bank")
    rows = {r["currency"]: r for r in freelancer_earnings(db, fid)
            if r["currency"]}
    assert rows["USD"]["expected_payout_cents"] == 22_500
    assert rows["EUR"]["expected_payout_cents"] == 22_500


# -- CLI ---------------------------------------------------------------

def test_cli_add_with_split(db, sample_project):
    r = CliRunner().invoke(cli, ["freelancer", "add", "--name", "Ali R.",
                                 "--split-pct", "70"])
    assert r.exit_code == 0, r.output
    assert "split 70%" in r.output


def test_cli_add_rejects_bad_split(db, sample_project):
    r = CliRunner().invoke(cli, ["freelancer", "add", "--name", "Ali R.",
                                 "--split-pct", "150"])
    assert r.exit_code != 0
    assert "between 0 and 100" in r.output


def test_cli_set_split(db, sample_project):
    runner = CliRunner()
    runner.invoke(cli, ["freelancer", "add", "--name", "Ali R."])
    r = runner.invoke(cli, ["freelancer", "set-split", "1",
                            "--split-pct", "65.5"])
    assert r.exit_code == 0, r.output
    assert "split 65.5%" in r.output
    r = runner.invoke(cli, ["freelancer", "show", "1"])
    assert r.exit_code == 0, r.output
    assert "split: 65.5% of collected earnings" in r.output


def test_cli_set_split_errors(db, sample_project):
    runner = CliRunner()
    r = runner.invoke(cli, ["freelancer", "set-split", "999",
                            "--split-pct", "50"])
    assert r.exit_code != 0
    assert "no freelancer #999" in r.output
    runner.invoke(cli, ["freelancer", "add", "--name", "Ali R."])
    r = runner.invoke(cli, ["freelancer", "set-split", "1",
                            "--split-pct", "-5"])
    assert r.exit_code != 0
    assert "between 0 and 100" in r.output


def test_cli_show_reports_share_lines(db, sample_project):
    runner = CliRunner()
    runner.invoke(cli, ["freelancer", "add", "--name", "Ali R.",
                        "--split-pct", "70"])
    iid = db.add_invoice(sample_project["id"], "final", 100_000, "USD",
                         status="sent", due_date="2026-01-01")
    db.assign_invoice_freelancer(iid, 1)
    db.record_payment(iid, 90_000, method="bank")
    r = runner.invoke(cli, ["freelancer", "show", "1"])
    assert r.exit_code == 0, r.output
    assert "split: 70% of collected earnings" in r.output
    assert "share (70%): 630.00" in r.output
    assert "still due to freelancer: 630.00" in r.output
    # and `freelancer list` shows the split too
    r = runner.invoke(cli, ["freelancer", "list"])
    assert r.exit_code == 0, r.output
    assert "70%" in r.output
