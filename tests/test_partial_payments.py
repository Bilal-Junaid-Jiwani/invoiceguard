"""Partial payments / payment-plan tests: ledger, CLI, dunning, dashboard."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.db import DB


def _sent_invoice(db, sample_project, cents=100_000, days_overdue=0):
    due = (date.today() - timedelta(days=days_overdue)).isoformat()
    iid = db.add_invoice(sample_project["id"], "deposit", cents, "USD",
                         status="sent", due_date=due)
    return iid


class TestRecordPayment:
    def test_partial_payment_sets_partially_paid(self, db, sample_project):
        iid = _sent_invoice(db, sample_project)
        res = db.record_payment(iid, 30_000, method="manual")
        assert res == {"invoice_id": iid, "paid_cents": 30_000,
                       "outstanding_cents": 70_000, "status": "partially-paid"}
        inv = db.get_invoice(iid)
        assert inv["status"] == "partially-paid"
        assert inv["paid_at"] is None
        assert db.paid_cents(iid) == 30_000
        assert db.outstanding_cents(iid) == 70_000

    def test_full_payment_marks_paid(self, db, sample_project):
        iid = _sent_invoice(db, sample_project)
        res = db.record_payment(iid, 100_000, method="bank", note="wire")
        assert res["status"] == "paid"
        assert res["outstanding_cents"] == 0
        inv = db.get_invoice(iid)
        assert inv["status"] == "paid"
        assert inv["paid_at"] is not None

    def test_two_partials_clear_balance(self, db, sample_project):
        iid = _sent_invoice(db, sample_project)
        db.record_payment(iid, 40_000)
        res = db.record_payment(iid, 60_000, method="stripe")
        assert res["status"] == "paid"
        assert db.outstanding_cents(iid) == 0
        rows = db.payments(iid)
        assert [r["amount_cents"] for r in rows] == [40_000, 60_000]
        assert rows[1]["method"] == "stripe"

    def test_payment_note_stored(self, db, sample_project):
        iid = _sent_invoice(db, sample_project)
        db.record_payment(iid, 10_000, note="check #1234")
        assert db.payments(iid)[0]["note"] == "check #1234"

    def test_overpayment_rejected(self, db, sample_project):
        iid = _sent_invoice(db, sample_project)
        db.record_payment(iid, 60_000)
        with pytest.raises(ValueError, match="exceeds the outstanding"):
            db.record_payment(iid, 50_000)
        # nothing was recorded and the balance is unchanged
        assert db.outstanding_cents(iid) == 40_000
        assert len(db.payments(iid)) == 1

    def test_non_positive_rejected(self, db, sample_project):
        iid = _sent_invoice(db, sample_project)
        with pytest.raises(ValueError, match="positive"):
            db.record_payment(iid, 0)
        with pytest.raises(ValueError, match="positive"):
            db.record_payment(iid, -100)

    def test_missing_invoice_rejected(self, db):
        with pytest.raises(ValueError, match="no invoice"):
            db.record_payment(999, 100)

    def test_void_invoice_rejected(self, db, sample_project):
        iid = _sent_invoice(db, sample_project)
        db.update_invoice(iid, status="void")
        with pytest.raises(ValueError, match="void"):
            db.record_payment(iid, 100)

    def test_already_paid_rejected(self, db, sample_project):
        iid = _sent_invoice(db, sample_project)
        db.record_payment(iid, 100_000)
        with pytest.raises(ValueError, match="already paid"):
            db.record_payment(iid, 1)

    def test_backfill_legacy_paid_invoice(self, db, sample_project):
        # Simulate a pre-0.2.0 database: paid invoice, no ledger rows.
        iid = _sent_invoice(db, sample_project)
        db.conn.execute(
            "UPDATE invoices SET status = 'paid' WHERE id = ?", (iid,))
        db.conn.commit()
        db.conn.execute("DELETE FROM payments")
        db.conn.commit()
        # Reopening the DB (like the CLI/dashboard would) backfills the row.
        db.close()
        reopened = DB(db.path)
        try:
            assert reopened.paid_cents(iid) == 100_000
            assert reopened.outstanding_cents(iid) == 0
            note = reopened.payments(iid)[0]["note"]
            assert "backfill" in note
        finally:
            reopened.close()


class TestRecordPaymentCLI:
    def test_cli_record_payment_partial_and_full(self, home, sample_project):
        r = CliRunner()
        db = DB(home / "invoiceguard.db")
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent")
        db.close()

        res = r.invoke(cli, ["invoice", "record-payment", str(iid),
                             "--amount", "250", "--note", "bank transfer"])
        assert res.exit_code == 0, res.output
        assert "$750.00 still outstanding" in res.output

        res = r.invoke(cli, ["invoice", "record-payment", str(iid),
                             "--amount", "750"])
        assert res.exit_code == 0, res.output
        assert "paid in full" in res.output

        assert "paid" in r.invoke(cli, ["invoice", "list"]).output

    def test_cli_record_payment_errors(self, home, sample_project):
        r = CliRunner()
        db = DB(home / "invoiceguard.db")
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent")
        db.close()

        assert r.invoke(cli, ["invoice", "record-payment", "4242",
                              "--amount", "10"]).exit_code != 0
        res = r.invoke(cli, ["invoice", "record-payment", str(iid),
                             "--amount", "1000000"])
        assert res.exit_code != 0
        assert "exceeds" in res.output

    def test_cli_mark_paid_records_ledger(self, home, sample_project):
        r = CliRunner()
        db = DB(home / "invoiceguard.db")
        iid = db.add_invoice(sample_project["id"], "deposit", 50_000,
                             "USD", status="sent")
        db.close()

        res = r.invoke(cli, ["invoice", "mark-paid", str(iid)])
        assert res.exit_code == 0, res.output

        db = DB(home / "invoiceguard.db")
        rows = db.payments(iid)
        assert len(rows) == 1
        assert rows[0]["amount_cents"] == 50_000
        assert rows[0]["method"] == "manual"
        db.close()

    def test_cli_mark_paid_already_paid_errors(self, home, sample_project):
        r = CliRunner()
        db = DB(home / "invoiceguard.db")
        iid = db.add_invoice(sample_project["id"], "deposit", 50_000,
                             "USD", status="sent")
        db.close()

        assert r.invoke(cli, ["invoice", "mark-paid", str(iid)]).exit_code == 0
        res = r.invoke(cli, ["invoice", "mark-paid", str(iid)])
        assert res.exit_code != 0
        assert "already paid" in res.output


class TestDunningWithPartials:
    def _check(self, monkeypatch):
        import invoiceguard.dunning as dunning

        sent = []
        monkeypatch.setattr(dunning, "send_email",
                            lambda cfg, to, subject, body: sent.append(
                                {"to": to, "subject": subject, "body": body}))
        return sent

    def test_partially_paid_still_dunned_with_outstanding(
            self, db, sample_project, smtp_cfg, home, monkeypatch):
        from invoiceguard.dunning import check_due
        sent = self._check(monkeypatch)

        due = (date.today() - timedelta(days=2)).isoformat()
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent", due_date=due)
        db.record_payment(iid, 40_000)

        results = check_due(db, smtp_cfg)
        assert len(results) == 1
        assert results[0]["stage"] == "day1"
        body = sent[0]["body"]
        assert "$1,000.00" in body          # original invoice amount
        assert "$400.00" in body            # paid so far
        assert "$600.00" in body            # remaining balance

    def test_fully_paid_excluded_from_overdue(self, db, sample_project):
        due = (date.today() - timedelta(days=10)).isoformat()
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent", due_date=due)
        db.record_payment(iid, 100_000)
        assert db.overdue_invoices() == []

    def test_overdue_invoices_carry_ledger(self, db, sample_project):
        due = (date.today() - timedelta(days=10)).isoformat()
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent", due_date=due)
        db.record_payment(iid, 25_000)
        rows = db.overdue_invoices()
        assert len(rows) == 1
        assert rows[0]["paid_cents"] == 25_000
        assert rows[0]["outstanding_cents"] == 75_000


class TestWebhookLedger:
    def _event(self, invoice_id, amount_total=None):
        obj = {"id": "cs_test_abc",
               "metadata": {"invoiceguard_invoice_id": str(invoice_id)}}
        if amount_total is not None:
            obj["amount_total"] = amount_total
        return {"type": "checkout.session.completed",
                "data": {"object": obj}}

    def test_full_stripe_payment_ledger(self, db, sample_project):
        from invoiceguard.webhooks import handle_event
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent")
        action = handle_event(db, self._event(iid, amount_total=100_000))
        assert action == "invoice_paid"
        rows = db.payments(iid)
        assert len(rows) == 1
        assert rows[0]["amount_cents"] == 100_000
        assert rows[0]["method"] == "stripe"
        assert db.get_invoice(iid)["status"] == "paid"

    def test_partial_stripe_payment(self, db, sample_project):
        from invoiceguard.webhooks import handle_event
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent")
        action = handle_event(db, self._event(iid, amount_total=30_000))
        assert action == "invoice_paid"
        assert db.get_invoice(iid)["status"] == "partially-paid"
        assert db.outstanding_cents(iid) == 70_000


class TestDashboardLedger:
    def _web_db(self, tmp_path, monkeypatch, seed):
        """Seed rows through the CLI DB, then read via web.db."""
        import invoiceguard.web.db as wdb
        monkeypatch.setenv("INVOICEGUARD_DB", str(seed))
        wdb.init_db()
        return wdb

    def test_list_totals_and_detail(self, home, sample_project, tmp_path,
                                    monkeypatch):
        path = home / "invoiceguard.db"
        db = DB(path)
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent", due_date="2026-01-01")
        db.record_payment(iid, 40_000)
        iid2 = db.add_invoice(sample_project["id"], "final", 200_000,
                              "USD", status="sent", due_date="2026-01-01")
        db.close()

        wdb = self._web_db(tmp_path, monkeypatch, path)
        rows = wdb.list_invoices()
        by_id = {r["id"]: r for r in rows}
        assert by_id[iid]["paid_cents"] == 40_000
        assert by_id[iid]["outstanding_cents"] == 60_000
        assert by_id[iid2]["outstanding_cents"] == 200_000

        totals = wdb.totals()
        assert totals["outstanding"] == 260_000
        assert totals["collected"] == 40_000

        inv = wdb.get_invoice(iid)
        assert inv["paid_cents"] == 40_000
        assert inv["outstanding_cents"] == 60_000
        pays = wdb.payments(iid)
        assert len(pays) == 1 and pays[0]["amount_cents"] == 40_000

        tl = wdb.build_timeline(inv, wdb.dunning_events(iid), pays)
        assert any("Payment received" in t["label"] for t in tl)

    def test_mark_invoice_paid_records_ledger(self, home, sample_project,
                                              tmp_path, monkeypatch):
        path = home / "invoiceguard.db"
        db = DB(path)
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent")
        db.close()

        wdb = self._web_db(tmp_path, monkeypatch, path)
        marked = wdb.mark_invoice_paid("cs_x", invoice_id_hint=iid,
                                       amount_cents=100_000)
        assert marked == iid
        assert wdb.payments(iid)[0]["method"] == "stripe"
        assert wdb.get_invoice(iid)["status"] == "paid"
