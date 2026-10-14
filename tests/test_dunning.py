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



class TestDunningPolicy:
    """Per-client dunning policies (v0.7.0, first agency-mode slice)."""

    def _client_project_invoice(self, db, name, email, days, monkeypatch=None):
        from invoiceguard.contracts import render_contract
        cid = db.add_client(name, email)
        contract = render_contract(name, email, "Work", 100_000, "USD",
                                   50.0, 1.5, 15)
        pid = db.add_project(cid, "Work", 100_000, "USD", 50.0, 1.5, 15,
                             contract)
        due = (date.today() - timedelta(days=days)).isoformat()
        iid = db.add_invoice(pid, "deposit", 100_000, "USD",
                             status="sent", due_date=due)
        db.update_invoice(iid, stripe_url="https://pay.stripe.test/x")
        return cid, iid

    def _fake_send(self, monkeypatch):
        sent = []

        def fake_send(cfg, to, subject, body):
            sent.append((to, subject))

        monkeypatch.setattr("invoiceguard.dunning.send_email", fake_send)
        return sent

    def test_stage_for_with_custom_thresholds(self):
        th = {"day1": 3, "day7": 10, "day15": 20}
        assert stage_for(2, set(), th) is None
        assert stage_for(3, set(), th) == "day1"
        assert stage_for(25, {"day1"}, th) == "day7"

    def test_stage_for_skips_disabled_stages(self):
        th = {"day1": 1, "day7": None, "day15": 15}
        assert stage_for(30, {"day1"}, th) == "day15"
        assert stage_for(30, set(), {"day1": None, "day7": None,
                                    "day15": None}) is None

    def test_custom_threshold_fires_at_custom_day(self, db, smtp_cfg,
                                                  monkeypatch, home):
        sent = self._fake_send(monkeypatch)
        cid, iid = self._client_project_invoice(
            db, "Slow", "slow@test.co", days=2)
        db.set_client_dunning_policy(cid, day1=3)
        assert check_due(db, smtp_cfg) == []  # 2 days < custom day1=3
        assert sent == []
        db.update_invoice(
            iid, due_date=(date.today() - timedelta(days=3)).isoformat())
        results = check_due(db, smtp_cfg)
        assert [r["stage"] for r in results] == ["day1"]
        assert len(sent) == 1

    def test_disabled_stage_stops_escalation(self, db, smtp_cfg,
                                            monkeypatch, home):
        sent = self._fake_send(monkeypatch)
        cid, iid = self._client_project_invoice(
            db, "NoFormal", "nf@test.co", days=20)
        db.set_client_dunning_policy(cid, day15=0)
        assert [r["stage"] for r in check_due(db, smtp_cfg)] == ["day1"]
        assert [r["stage"] for r in check_due(db, smtp_cfg)] == ["day7"]
        assert check_due(db, smtp_cfg) == []  # day15 disabled: done
        assert len(sent) == 2

    def test_all_disabled_invoice_reported_skipped(self, db, smtp_cfg,
                                                   monkeypatch, home):
        sent = self._fake_send(monkeypatch)
        cid, iid = self._client_project_invoice(
            db, "VIP", "vip@test.co", days=30)
        db.set_client_dunning_policy(cid, day1=0, day7=0, day15=0)
        results = check_due(db, smtp_cfg)
        assert len(results) == 1
        assert results[0]["stage"] is None
        assert "dunning disabled for this client" in results[0]["skipped"]
        assert sent == []
        assert db.sent_stages(iid) == set()  # nothing recorded

    def test_policy_is_per_client(self, db, smtp_cfg, monkeypatch, home):
        sent = self._fake_send(monkeypatch)
        cid_slow, _ = self._client_project_invoice(
            db, "Slow", "slow@test.co", days=2)
        _, _ = self._client_project_invoice(
            db, "Fast", "fast@test.co", days=2)
        db.set_client_dunning_policy(cid_slow, day1=10, day7=14, day15=21)
        results = check_due(db, smtp_cfg)
        # only the default-policy client fires day1 at 2 days overdue
        assert len(results) == 1
        assert results[0]["to"] == "fast@test.co"
        assert len(sent) == 1

    def test_null_keeps_defaults(self, db, sample_client):
        assert db.set_client_dunning_policy(sample_client["id"]) == {
            "day1": 1, "day7": 7, "day15": 15}

    def test_reset_restores_defaults(self, db, sample_client):
        db.set_client_dunning_policy(sample_client["id"], day1=3)
        assert db.reset_client_dunning_policy(sample_client["id"]) == {
            "day1": 1, "day7": 7, "day15": 15}
        assert db.get_client(sample_client["id"])["dunning_day1"] is None

    def test_negative_threshold_rejected(self, db, sample_client):
        with pytest.raises(ValueError, match=">= 0"):
            db.set_client_dunning_policy(sample_client["id"], day7=-1)

    def test_garbage_threshold_rejected(self, db, sample_client):
        with pytest.raises(ValueError, match="whole number"):
            db.set_client_dunning_policy(sample_client["id"], day7="soon")

    def test_out_of_order_thresholds_rejected(self, db, sample_client):
        with pytest.raises(ValueError, match="escalation must stay"):
            db.set_client_dunning_policy(sample_client["id"],
                                         day1=10, day7=5)

    def test_disabled_stage_allows_uneven_order(self, db, sample_client):
        # day1 disabled: only enabled stages (day7, day15) must stay ordered
        policy = db.set_client_dunning_policy(sample_client["id"],
                                              day1=0, day7=5)
        assert policy == {"day1": None, "day7": 5, "day15": 15}

    def test_unknown_client_rejected(self, db):
        with pytest.raises(ValueError, match="no client #99"):
            db.set_client_dunning_policy(99, day1=3)

    def test_migration_adds_columns_to_old_db(self, tmp_path):
        # DB created before v0.7.0 (no policy columns) gets them on connect.
        import sqlite3
        p = tmp_path / "old.db"
        conn = sqlite3.connect(str(p))
        conn.execute(
            "CREATE TABLE clients(id INTEGER PRIMARY KEY, name TEXT NOT "
            "NULL, email TEXT, phone TEXT, created_at TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO clients(name, created_at) VALUES ('Old', 'x')")
        conn.commit()
        conn.close()
        d = DB(p)
        try:
            cols = {r[1] for r in d.conn.execute("PRAGMA table_info(clients)")}
            assert {"dunning_day1", "dunning_day7", "dunning_day15"} <= cols
            assert d.set_client_dunning_policy(1, day1=2)["day1"] == 2
        finally:
            d.close()
