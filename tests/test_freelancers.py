"""Agency-mode slice #2: freelancer roster + invoice attribution."""

from __future__ import annotations

import os
import sqlite3

import pytest

from invoiceguard.db import DB, migrate_invoices_freelancer_id
from invoiceguard.reports import receivables_report


def test_add_and_list_freelancers(db):
    fid = db.add_freelancer("Ali R.", "ali@example.test", "+15551234567")
    assert fid == 1
    rows = db.list_freelancers()
    assert len(rows) == 1
    assert rows[0]["name"] == "Ali R."
    assert rows[0]["email"] == "ali@example.test"
    assert rows[0]["phone"] == "+15551234567"


def test_add_freelancer_requires_name(db):
    with pytest.raises(ValueError, match="name is required"):
        db.add_freelancer("   ")


def test_set_freelancer_contact(db):
    fid = db.add_freelancer("Sana K.")
    db.set_freelancer_email(fid, "sana@example.test")
    db.set_freelancer_phone(fid, "+15557654321")
    f = db.get_freelancer(fid)
    assert f["email"] == "sana@example.test"
    assert f["phone"] == "+15557654321"
    db.set_freelancer_email(fid, None)
    assert db.get_freelancer(fid)["email"] is None
    assert db.get_freelancer(999) is None


def test_assign_invoice_freelancer(db, sample_project):
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                         "USD", status="sent", due_date="2026-01-01")
    fid = db.add_freelancer("Ali R.")
    db.assign_invoice_freelancer(iid, fid)
    full = db.invoice_full(iid)
    assert full["freelancer_id"] == fid
    assert full["freelancer_name"] == "Ali R."
    # clearing attribution
    db.assign_invoice_freelancer(iid, None)
    full = db.invoice_full(iid)
    assert full["freelancer_id"] is None
    assert full["freelancer_name"] is None


def test_assign_rejects_unknown_ids(db, sample_project):
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                         "USD", status="sent", due_date="2026-01-01")
    with pytest.raises(ValueError, match="no invoice #999"):
        db.assign_invoice_freelancer(999, None)
    with pytest.raises(ValueError, match="no freelancer #999"):
        db.assign_invoice_freelancer(iid, 999)
    # failed attribution writes nothing
    assert db.get_invoice(iid)["freelancer_id"] is None


def test_attribution_does_not_touch_state(db, sample_project):
    """Attribution is pure bookkeeping: status/dunning/payments unchanged."""
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                         "USD", status="sent", due_date="2026-01-01")
    fid = db.add_freelancer("Ali R.")
    db.assign_invoice_freelancer(iid, fid)
    inv = db.get_invoice(iid)
    assert inv["status"] == "sent"
    assert inv["freelancer_id"] == fid
    db.record_payment(iid, 40_000, method="bank")
    assert db.get_invoice(iid)["status"] == "partially-paid"
    assert db.get_invoice(iid)["freelancer_id"] == fid


def test_migration_adds_column_to_old_db(home):
    """A pre-v0.8.0 database gets invoices.freelancer_id on connect."""
    path = os.environ["INVOICEGUARD_DB"]
    conn = sqlite3.connect(path)
    # A faithful pre-v0.8.0 invoices table: every 0.7.1 column EXCEPT
    # freelancer_id.
    conn.execute(
        "CREATE TABLE invoices(id INTEGER PRIMARY KEY, "
        "project_id INTEGER NOT NULL, kind TEXT NOT NULL, "
        "amount_cents INTEGER NOT NULL, currency TEXT DEFAULT 'USD', "
        "status TEXT DEFAULT 'draft', stripe_url TEXT, "
        "stripe_session_id TEXT, due_date TEXT, sent_at TEXT, "
        "paid_at TEXT, created_at TEXT NOT NULL)")
    conn.commit()
    migrate_invoices_freelancer_id(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
    conn.close()
    assert "freelancer_id" in cols
    # idempotent: reopening via DB does not lose data
    d = DB(path)
    assert {r[1] for r in
            d.conn.execute("PRAGMA table_info(invoices)")} == cols
    d.close()


def test_list_invoices_joins_freelancer_name(db, sample_project):
    i1 = db.add_invoice(sample_project["id"], "deposit", 100_000,
                        "USD", status="sent", due_date="2026-01-01")
    i2 = db.add_invoice(sample_project["id"], "final", 100_000,
                        "USD", status="sent", due_date="2026-02-01")
    fid = db.add_freelancer("Ali R.")
    db.assign_invoice_freelancer(i1, fid)
    rows = {r["id"]: r for r in db.list_invoices()}
    assert rows[i1]["freelancer_name"] == "Ali R."
    assert rows[i2]["freelancer_name"] is None


def test_report_by_freelancer_and_filter(db, sample_project):
    i1 = db.add_invoice(sample_project["id"], "deposit", 100_000,
                        "USD", status="sent", due_date="2026-01-01")
    i2 = db.add_invoice(sample_project["id"], "final", 200_000,
                        "USD", status="sent", due_date="2026-01-01")
    fid = db.add_freelancer("Ali R.")
    db.assign_invoice_freelancer(i1, fid)
    rep = receivables_report(db, as_of="2026-03-01")
    by_f = {r["freelancer_name"]: r for r in rep["by_freelancer"]}
    assert by_f["Ali R."]["outstanding_cents"] == 100_000
    assert by_f["unassigned"]["outstanding_cents"] == 200_000
    filtered = receivables_report(db, as_of="2026-03-01",
                                  freelancer_id=fid)
    assert [r["id"] for r in filtered["rows"]] == [i1]
    assert filtered["totals"]["USD"]["outstanding_cents"] == 100_000


# -- CLI -----------------------------------------------------------------
from click.testing import CliRunner  # noqa: E402

from invoiceguard.cli import cli  # noqa: E402


def test_cli_freelancer_roundtrip(db, sample_project):
    runner = CliRunner()
    r = runner.invoke(cli, ["freelancer", "add", "--name", "Ali R.",
                            "--email", "ali@example.test"])
    assert r.exit_code == 0, r.output
    assert "freelancer #1: Ali R." in r.output
    r = runner.invoke(cli, ["freelancer", "set-email", "1",
                            "--email", "new@example.test"])
    assert r.exit_code == 0, r.output
    r = runner.invoke(cli, ["freelancer", "set-phone", "1",
                            "--phone", "not-a-number"])
    assert r.exit_code != 0
    r = runner.invoke(cli, ["freelancer", "list"])
    assert r.exit_code == 0, r.output
    assert "Ali R." in r.output and "new@example.test" in r.output


def test_cli_assign_and_list_filter(db, sample_project):
    runner = CliRunner()
    runner.invoke(cli, ["freelancer", "add", "--name", "Ali R."])
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                         "USD", status="sent", due_date="2026-01-01")
    r = runner.invoke(cli, ["invoice", "assign", str(iid),
                            "--freelancer", "1"])
    assert r.exit_code == 0, r.output
    assert "attributed to freelancer #1" in r.output
    r = runner.invoke(cli, ["invoice", "list", "--freelancer", "1"])
    assert r.exit_code == 0, r.output
    assert "Ali R." in r.output
    r = runner.invoke(cli, ["invoice", "list", "--freelancer", "999"])
    assert r.exit_code != 0
    assert "no freelancer #999" in r.output
    # mutual exclusion and missing-id errors
    r = runner.invoke(cli, ["invoice", "assign", str(iid), "--none"])
    assert r.exit_code == 0, r.output
    assert "attribution cleared" in r.output
    r = runner.invoke(cli, ["invoice", "assign", "999", "--freelancer", "1"])
    assert r.exit_code != 0
    assert "no invoice #999" in r.output
    r = runner.invoke(cli, ["invoice", "assign", str(iid)])
    assert r.exit_code != 0
    r = runner.invoke(cli, ["invoice", "report", "--freelancer", "1",
                            "--csv", "-"])
    assert r.exit_code == 0, r.output
    header = r.output.splitlines()[0]
    assert header.endswith("freelancer")
