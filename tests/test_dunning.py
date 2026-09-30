"""Dunning engine tests: stage selection, idempotency, templates."""

from datetime import date, timedelta

import pytest

from invoiceguard.db import DB
from invoiceguard.dunning import (STAGES, check_due, render_template,
                                  stage_for)


class TestStageFor:
    def test_nothing_due_yet(self):
        assert stage_for(0, set()) is None

    def test_day1_at_one_day(self):
        assert stage_for(1, set()) == "day1"

    def test_escalates_day7(self):
        assert stage_for(7, {"day1"}) == "day7"

    def test_escalates_day15(self):
        assert stage_for(15, {"day1", "day7"}) == "day15"

    def test_backdated_invoice_escalates_in_order(self):
        # 20 days overdue, nothing sent: starts polite, not formal
        assert stage_for(20, set()) == "day1"
        assert stage_for(20, {"day1"}) == "day7"
        assert stage_for(20, {"day1", "day7"}) == "day15"

    def test_idempotent_all_sent(self):
        assert stage_for(60, set(STAGES)) is None

    def test_skips_sent_stage(self):
        # day1 somehow already sent, day7 threshold met -> day7 next
        assert stage_for(8, {"day1"}) == "day7"


class TestRenderTemplate:
    def test_all_vars_render(self, home):
        path = home / "templates" / "day1.md"
        body = render_template(path, {
            "client_name": "Acme", "project_title": "Site",
            "amount": "$1,000.00", "due_date": "2026-09-20",
            "days_overdue": 1, "late_fee_pct": 1.5,
            "pay_url": "https://pay.stripe.test/x",
        })
        assert "{client_name}" not in body
        assert "Acme" in body and "Site" in body
        assert "https://pay.stripe.test/x" in body

    def test_unknown_var_raises(self, home, tmp_path):
        bad = tmp_path / "bad.md"
        bad.write_text("hello {nonexistent_var}")
        with pytest.raises(ValueError, match="unknown variable"):
            render_template(bad, {})


class TestCheckDueIdempotency:
    def _overdue_invoice(self, db, sample_project, days):
        due = (date.today() - timedelta(days=days)).isoformat()
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent", due_date=due)
        db.update_invoice(iid, stripe_url="https://pay.stripe.test/deposit1")
        return iid

    def test_never_resends_a_stage(self, db, sample_project, smtp_cfg, home,
                                   monkeypatch):
        sent = []

        def fake_send(cfg, to, subject, body):
            sent.append((to, subject))

        monkeypatch.setattr("invoiceguard.dunning.send_email", fake_send)

        iid = self._overdue_invoice(db, sample_project, days=20)
        # run 1 -> day1, run 2 -> day7, run 3 -> day15, run 4 -> nothing
        assert [r["stage"] for r in check_due(db, smtp_cfg)] == ["day1"]
        assert [r["stage"] for r in check_due(db, smtp_cfg)] == ["day7"]
        assert [r["stage"] for r in check_due(db, smtp_cfg)] == ["day15"]
        assert check_due(db, smtp_cfg) == []
        assert len(sent) == 3
        assert db.sent_stages(iid) == {"day1", "day7", "day15"}

    def test_flips_status_to_overdue(self, db, sample_project, smtp_cfg,
                                     monkeypatch):
        monkeypatch.setattr("invoiceguard.dunning.send_email",
                            lambda *a: None)
        iid = self._overdue_invoice(db, sample_project, days=2)
        check_due(db, smtp_cfg)
        assert db.get_invoice(iid)["status"] == "overdue"

    def test_not_yet_due_skipped(self, db, sample_project, smtp_cfg,
                                 monkeypatch):
        monkeypatch.setattr("invoiceguard.dunning.send_email",
                            lambda *a: (_ for _ in ()).throw(
                                AssertionError("must not send")))
        future = (date.today() + timedelta(days=3)).isoformat()
        db.add_invoice(sample_project["id"], "deposit", 100_000, "USD",
                       status="sent", due_date=future)
        assert check_due(db, smtp_cfg) == []

    def test_paid_invoices_ignored(self, db, sample_project, smtp_cfg,
                                   monkeypatch):
        monkeypatch.setattr("invoiceguard.dunning.send_email",
                            lambda *a: (_ for _ in ()).throw(
                                AssertionError("must not send")))
        past = (date.today() - timedelta(days=30)).isoformat()
        db.add_invoice(sample_project["id"], "deposit", 100_000, "USD",
                       status="paid", due_date=past)
        assert check_due(db, smtp_cfg) == []

    def test_day15_quotes_late_fee_clause(self, db, sample_project, smtp_cfg,
                                          home, monkeypatch):
        bodies = []
        monkeypatch.setattr(
            "invoiceguard.dunning.send_email",
            lambda cfg, to, subject, body: bodies.append(body))
        iid = self._overdue_invoice(db, sample_project, days=16)
        db.record_dunning(iid, "day1")
        db.record_dunning(iid, "day7")
        check_due(db, smtp_cfg)
        assert bodies and "1.5% per month" in bodies[0]
