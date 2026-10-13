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
            "amount": "$1,000.00", "paid": "$0.00", "outstanding": "$1,000.00",
            "due_date": "2026-09-20",
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


class TestCheckDueSkipsEmailLessClients:
    """check-due must not crash when a client has no email (0.6.3).

    Regression: send_email() raises ValueError on an empty recipient,
    which aborted the whole dunning run — every other overdue invoice
    never got its email. Now the stage is skipped with a reported
    reason, and adding the email later re-sends the stage.
    """

    def _client_project_invoice(self, db, name, email, days):
        cid = db.add_client(name, email)
        pid = db.add_project(cid, "Work", 100_000, contract_md="x")
        due = (date.today() - timedelta(days=days)).isoformat()
        iid = db.add_invoice(pid, "final", 100_000, "USD", status="sent",
                             due_date=due)
        db.update_invoice(iid, stripe_url="https://pay.stripe.test/x")
        return iid

    def test_email_less_client_is_skipped_not_crashed(
            self, db, smtp_cfg, home, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "invoiceguard.dunning.send_email",
            lambda cfg, to, subject, body: sent.append(to))

        iid_ok = self._client_project_invoice(
            db, "Has Email", "billing@has.test", days=2)
        iid_skip = self._client_project_invoice(
            db, "No Email", None, days=2)

        results = check_due(db, smtp_cfg)  # must not raise

        by_id = {r["invoice_id"]: r for r in results}
        assert by_id[iid_ok]["stage"] == "day1"
        assert by_id[iid_ok]["to"] == "billing@has.test"
        assert "skipped" not in by_id[iid_ok]

        skip = by_id[iid_skip]
        assert skip["stage"] == "day1"
        assert skip["to"] is None
        assert "no email" in skip["skipped"]

        # the good client still got its email; nothing was recorded
        # for the skipped invoice, so the stage goes out later once
        # an email is on file.
        assert sent == ["billing@has.test"]
        assert db.sent_stages(iid_skip) == set()
        assert db.get_invoice(iid_skip)["status"] == "sent"

    def test_stage_sends_after_email_added(
            self, db, smtp_cfg, home, monkeypatch):
        sent = []
        monkeypatch.setattr(
            "invoiceguard.dunning.send_email",
            lambda cfg, to, subject, body: sent.append(to))
        iid = self._client_project_invoice(db, "No Email", None, days=2)
        assert check_due(db, smtp_cfg)[0].get("skipped")
        # find the client through the project
        pid = db.get_invoice(iid)["project_id"]
        cid = db.get_project(pid)["client_id"]
        db.set_client_email(cid, "now@here.test")
        results = check_due(db, smtp_cfg)
        assert results[0]["stage"] == "day1"
        assert results[0]["to"] == "now@here.test"
        assert sent == ["now@here.test"]


class TestCheckDueSendFailureIsolation:
    """check-due must not abort the run when one email send fails (0.6.5).

    Regression: a transient SMTP error on one invoice's send (a relay
    hiccup, a rejected recipient, ...) propagated out of check_due and
    aborted the whole collections run — every other overdue invoice never
    got its stage that run. Now the failure is isolated per invoice:
    reported in the result dict, nothing recorded for the failed invoice
    (so the stage retries on the next run), and the rest of the batch
    still goes out.
    """

    def _client_project_invoice(self, db, name, email, days):
        cid = db.add_client(name, email)
        pid = db.add_project(cid, "Work", 100_000, contract_md="x")
        due = (date.today() - timedelta(days=days)).isoformat()
        iid = db.add_invoice(pid, "final", 100_000, "USD", status="sent",
                             due_date=due)
        db.update_invoice(iid, stripe_url="https://pay.stripe.test/x")
        return iid

    def test_one_failed_send_does_not_abort_run(
            self, db, smtp_cfg, home, monkeypatch):
        import smtplib

        sent = []

        def flaky_send(cfg, to, subject, body):
            if to == "alice@flaky.test":
                raise smtplib.SMTPException("421 transient failure")
            sent.append(to)

        monkeypatch.setattr("invoiceguard.dunning.send_email", flaky_send)

        iid_bad = self._client_project_invoice(
            db, "Alice", "alice@flaky.test", days=2)
        iid_ok = self._client_project_invoice(
            db, "Bob", "bob@steady.test", days=2)

        results = check_due(db, smtp_cfg)  # must not raise

        by_id = {r["invoice_id"]: r for r in results}
        failed = by_id[iid_bad]
        assert failed["stage"] == "day1"
        assert "transient failure" in failed["send_error"]
        # nothing recorded for the failed invoice: stage retries next run
        assert db.sent_stages(iid_bad) == set()
        assert db.get_invoice(iid_bad)["status"] == "sent"

        # the rest of the batch still went out normally
        good = by_id[iid_ok]
        assert good["stage"] == "day1"
        assert good["to"] == "bob@steady.test"
        assert sent == ["bob@steady.test"]
        assert db.sent_stages(iid_ok) == {"day1"}
        assert db.get_invoice(iid_ok)["status"] == "overdue"

    def test_failed_stage_retries_on_next_run(
            self, db, smtp_cfg, home, monkeypatch):
        import smtplib

        calls = []

        def flaky_send(cfg, to, subject, body):
            calls.append(to)
            if len(calls) == 1:
                raise smtplib.SMTPException("421 transient failure")

        monkeypatch.setattr("invoiceguard.dunning.send_email", flaky_send)

        iid = self._client_project_invoice(
            db, "Alice", "alice@flaky.test", days=2)
        first = check_due(db, smtp_cfg)
        assert first[0].get("send_error")
        second = check_due(db, smtp_cfg)  # relay is fine now
        assert second[0]["stage"] == "day1"
        assert second[0]["to"] == "alice@flaky.test"
        assert "send_error" not in second[0]
        assert db.sent_stages(iid) == {"day1"}

