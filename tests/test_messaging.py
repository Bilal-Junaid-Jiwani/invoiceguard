"""SMS/WhatsApp escalation tests: phone handling, Twilio send (mocked
HTTP layer — no network), config resolution, dunning integration, CLI."""

from datetime import date, timedelta

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.dunning import check_due
from invoiceguard.messaging import (message_channel, messaging_configured,
                                    messaging_from_config, normalize_phone,
                                    render_message, send_message)

TWILIO_CFG = {
    "account_sid": "ACtest123",
    "auth_token": "test-token",
    "from_number": "+15550001111",
}


class TestNormalizePhone:
    def test_e164_passthrough(self):
        assert normalize_phone("+15551234567") == "+15551234567"

    def test_strips_formatting(self):
        assert normalize_phone("+1 (555) 123-4567") == "+15551234567"

    def test_adds_plus_to_bare_digits(self):
        assert normalize_phone("15551234567") == "+15551234567"

    def test_double_zero_prefix(self):
        assert normalize_phone("00442079460018") == "+442079460018"

    @pytest.mark.parametrize("bad", [None, "", "abc", "+123", "call me",
                                     "+1234567890123456"])
    def test_invalid_numbers(self, bad):
        assert normalize_phone(bad) is None


class TestConfigResolution:
    def test_unconfigured_by_default(self):
        assert messaging_configured({}) is False
        assert messaging_configured(None) is False

    def test_placeholders_count_as_unconfigured(self):
        cfg = {"account_sid": "AC_...", "auth_token": "your-auth-token",
               "from_number": "+10000000000"}
        assert messaging_configured(cfg) is False

    def test_env_overrides_file(self, monkeypatch):
        monkeypatch.setenv("INVOICEGUARD_TWILIO_ACCOUNT_SID", "ACenv")
        cfg = messaging_from_config({"messaging": dict(TWILIO_CFG)})
        assert cfg["account_sid"] == "ACenv"
        assert cfg["auth_token"] == "test-token"

    def test_channel_defaults_and_validation(self):
        assert message_channel({}) == "sms"
        assert message_channel({"channel": "whatsapp"}) == "whatsapp"
        assert message_channel({"channel": "pigeon"}) == "sms"


class TestSendMessage:
    def _capture(self, monkeypatch):
        calls = []

        def fake_post(url, data, sid, token):
            calls.append({"url": url, "data": data,
                          "sid": sid, "token": token})
            return {"sid": "SMtest999", "status": "queued"}

        monkeypatch.setattr("invoiceguard.messaging._post_form", fake_post)
        return calls

    def test_sms_payload(self, monkeypatch):
        calls = self._capture(monkeypatch)
        reply = send_message(TWILIO_CFG, "+1 (555) 123-4567",
                             "please pay", channel="sms")
        assert reply["sid"] == "SMtest999"
        assert calls[0]["data"] == {"To": "+15551234567",
                                    "From": "+15550001111",
                                    "Body": "please pay"}
        assert calls[0]["url"].endswith("/Accounts/ACtest123/Messages.json")

    def test_whatsapp_prefixes(self, monkeypatch):
        calls = self._capture(monkeypatch)
        send_message(TWILIO_CFG, "+15551234567", "hi", channel="whatsapp")
        assert calls[0]["data"]["To"] == "whatsapp:+15551234567"
        assert calls[0]["data"]["From"] == "whatsapp:+15550001111"

    def test_unconfigured_raises(self):
        with pytest.raises(ValueError, match="not configured"):
            send_message({}, "+15551234567", "hi")

    def test_invalid_phone_raises(self):
        with pytest.raises(ValueError, match="invalid phone"):
            send_message(TWILIO_CFG, "not-a-number", "hi")


class TestDunningMessageEscalation:
    def _day15_invoice(self, db, sample_project, with_phone=True):
        """Invoice 20 days overdue with day1/day7 already sent -> day15."""
        if with_phone:
            db.set_client_phone(sample_project["client_id"],
                                "+15551234567")
        due = (date.today() - timedelta(days=20)).isoformat()
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent", due_date=due)
        db.update_invoice(iid, stripe_url="https://pay.stripe.test/x")
        db.record_dunning(iid, "day1")
        db.record_dunning(iid, "day7")
        return iid

    def test_day15_also_sends_message(self, db, sample_project, smtp_cfg,
                                     monkeypatch):
        iid = self._day15_invoice(db, sample_project)
        sent = []
        monkeypatch.setattr("invoiceguard.dunning.send_email",
                            lambda *a: None)
        monkeypatch.setattr(
            "invoiceguard.dunning.send_message",
            lambda cfg, to, body, channel=None: sent.append(
                (to, body, channel)) or {"sid": "SM1"})
        results = check_due(db, smtp_cfg, messaging_cfg=TWILIO_CFG)
        assert results[0]["stage"] == "day15"
        assert results[0]["message_channel"] == "sms"
        assert sent and sent[0][0] == "+15551234567"
        assert "$" in sent[0][1]  # money rendered into the message
        assert db.message_sent(iid, "day15", "sms")
        assert db.messages(iid)[0]["provider_sid"] == "SM1"

    def test_message_sent_once(self, db, sample_project, smtp_cfg,
                               monkeypatch):
        iid = self._day15_invoice(db, sample_project)
        monkeypatch.setattr("invoiceguard.dunning.send_email",
                            lambda *a: None)
        monkeypatch.setattr(
            "invoiceguard.dunning.send_message",
            lambda cfg, to, body, channel=None: {"sid": "SM1"})
        check_due(db, smtp_cfg, messaging_cfg=TWILIO_CFG)
        db.record_dunning(iid, "day15")  # pretend a later stage existed
        assert len(db.messages(iid)) == 1

    def test_no_phone_no_message_no_error(self, db, sample_project,
                                          smtp_cfg, monkeypatch):
        self._day15_invoice(db, sample_project, with_phone=False)
        monkeypatch.setattr("invoiceguard.dunning.send_email",
                            lambda *a: None)
        monkeypatch.setattr(
            "invoiceguard.dunning.send_message",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("must not send")))
        results = check_due(db, smtp_cfg, messaging_cfg=TWILIO_CFG)
        assert results[0]["stage"] == "day15"
        assert "message_to" not in results[0]

    def test_unconfigured_stays_email_only(self, db, sample_project,
                                           smtp_cfg, monkeypatch):
        self._day15_invoice(db, sample_project)
        monkeypatch.setattr("invoiceguard.dunning.send_email",
                            lambda *a: None)
        monkeypatch.setattr(
            "invoiceguard.dunning.send_message",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("must not send")))
        results = check_due(db, smtp_cfg, messaging_cfg={})
        assert "message_to" not in results[0]

    def test_message_failure_does_not_block_email(
            self, db, sample_project, smtp_cfg, monkeypatch):
        iid = self._day15_invoice(db, sample_project)
        monkeypatch.setattr("invoiceguard.dunning.send_email",
                            lambda *a: None)

        def boom(cfg, to, body, channel=None):
            raise RuntimeError("Twilio API error 500")

        monkeypatch.setattr("invoiceguard.dunning.send_message", boom)
        results = check_due(db, smtp_cfg, messaging_cfg=TWILIO_CFG)
        assert results[0]["stage"] == "day15"  # email stage still recorded
        assert "message_error" in results[0]
        assert db.sent_stages(iid) == {"day1", "day7", "day15"}


class TestNotifyCLI:
    def _setup(self, db, sample_project, runner_env=None):
        db.set_client_phone(sample_project["client_id"], "+15551234567")
        due = (date.today() - timedelta(days=20)).isoformat()
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent", due_date=due)
        db.update_invoice(iid, stripe_url="https://pay.stripe.test/x")
        return iid

    def test_dry_run_renders_without_credentials(self, db, sample_project):
        iid = self._setup(db, sample_project)
        res = CliRunner().invoke(cli, ["invoice", "notify", str(iid),
                                       "--dry-run"])
        assert res.exit_code == 0, res.output
        assert "[dry-run]" in res.output
        assert "InvoiceGuard: Hi Acme Corp" in res.output
        assert db.messages(iid) == []  # dry-run records nothing

    def test_notify_sends_and_records(self, db, sample_project,
                                      monkeypatch):
        iid = self._setup(db, sample_project)
        for k, v in [("INVOICEGUARD_TWILIO_ACCOUNT_SID", "ACtest123"),
                     ("INVOICEGUARD_TWILIO_AUTH_TOKEN", "test-token"),
                     ("INVOICEGUARD_TWILIO_FROM_NUMBER", "+15550001111")]:
            monkeypatch.setenv(k, v)
        monkeypatch.setattr(
            "invoiceguard.messaging._post_form",
            lambda url, data, sid, token: {"sid": "SMcli1"})
        res = CliRunner().invoke(cli, ["invoice", "notify", str(iid),
                                       "--channel", "whatsapp"])
        assert res.exit_code == 0, res.output
        assert "sent whatsapp to +15551234567" in res.output
        rows = db.messages(iid)
        assert rows and rows[0]["channel"] == "whatsapp"
        assert rows[0]["provider_sid"] == "SMcli1"

    def test_notify_without_phone_errors(self, db, sample_project):
        iid = db.add_invoice(sample_project["id"], "deposit", 100_000,
                             "USD", status="sent", due_date="2026-01-01")
        res = CliRunner().invoke(cli, ["invoice", "notify", str(iid)])
        assert res.exit_code != 0
        assert "no valid phone" in res.output

    def test_client_add_and_set_phone(self, db):
        res = CliRunner().invoke(
            cli, ["client", "add", "--name", "Phone Co",
                  "--email", "p@phone.test", "--phone", "+1 555 123 4567"])
        assert res.exit_code == 0, res.output
        client = [c for c in db.list_clients()
                  if c["name"] == "Phone Co"][0]
        assert client["phone"] == "+15551234567"
        res = CliRunner().invoke(
            cli, ["client", "set-phone", str(client["id"]),
                  "--phone", "+442079460018"])
        assert res.exit_code == 0, res.output
        assert db.get_client(client["id"])["phone"] == "+442079460018"

    def test_client_add_rejects_bad_phone(self, db):
        res = CliRunner().invoke(
            cli, ["client", "add", "--name", "Bad", "--phone", "hello"])
        assert res.exit_code != 0
        assert "invalid phone" in res.output


class TestRenderMessage:
    def test_all_vars_render(self):
        body = render_message({
            "client_name": "Acme", "project_title": "Site",
            "outstanding": "$1,000.00", "days_overdue": 20,
            "due_date": "2026-09-20",
            "total_with_late_fees": "$1,030.23",
            "pay_url": "https://pay.stripe.test/x",
        })
        assert "Acme" in body and "$1,030.23" in body
        assert "https://pay.stripe.test/x" in body
        assert len(body) < 320  # stays a short message, not an email
