"""0.8.4 regression tests for the external hands-on review findings.

The reviewer installed the package with stripe 15.6.1 and ran it live:
the Stripe webhook silently never recorded payments, delayed-payment
events marked invoices paid early, failed invoice creates left orphan
drafts, sign-status contradicted the ack state, and dunning emails
leaked template markup. Each test below pins one of those behaviors.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import types

import pytest
import stripe
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.db import DB
from invoiceguard.mail import to_plain_text
from invoiceguard.webhooks import handle_event, verify_event

TEST_SECRET = "whsec_test_0123456789abcdef"


def _sign(payload: bytes, secret: str = TEST_SECRET) -> str:
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _event_bytes(invoice_id: int, payment_status=None,
                 etype="checkout.session.completed", event_id="evt_x"):
    obj = {
        "id": "cs_test_obj_1",
        "amount_total": 100_000,
        "metadata": {"invoiceguard_invoice_id": str(invoice_id)},
    }
    if payment_status is not None:
        obj["payment_status"] = payment_status
    return json.dumps({"id": event_id, "type": etype,
                       "data": {"object": obj}}).encode()


# -- webhook: real StripeObject events (webhooks.py) --------------------

def test_handle_event_accepts_real_stripe_object(db, sample_project):
    """verify_event returns a StripeObject Event under stripe>=8; the
    old handle_event called .get() on it and would have crashed with
    AttributeError on any real (non-dict) event."""
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000, "USD",
                         status="sent")
    payload = _event_bytes(iid, event_id="evt_real_obj")
    event = verify_event(payload, _sign(payload), TEST_SECRET)
    assert not isinstance(event, dict)  # a genuine StripeObject
    assert handle_event(db, event) == "invoice_paid"
    assert db.get_invoice(iid)["status"] == "paid"


def test_handle_event_unpaid_records_nothing(db, sample_project):
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000, "USD",
                         status="sent")
    evt = json.loads(_event_bytes(iid, payment_status="unpaid",
                                  event_id="evt_unpaid"))
    assert handle_event(db, evt) == "ignored:payment_unpaid"
    assert db.get_invoice(iid)["status"] == "sent"
    assert db.payments(iid) == []


def test_handle_event_async_success_after_unpaid(db, sample_project):
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000, "USD",
                         status="sent")
    un = json.loads(_event_bytes(iid, payment_status="unpaid",
                                 event_id="evt_ach_1"))
    assert handle_event(db, un) == "ignored:payment_unpaid"
    ok = json.loads(_event_bytes(
        iid, payment_status="paid",
        etype="checkout.session.async_payment_succeeded",
        event_id="evt_ach_2"))
    assert handle_event(db, ok) == "invoice_paid"
    assert db.get_invoice(iid)["status"] == "paid"
    assert len(db.payments(iid)) == 1


# -- invoice create: failed link creation leaves no draft ---------------

def _seed_cli(home, monkeypatch, link_create=None):
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    r.invoke(cli, ["project", "create", "--client", "Hooli",
                   "--title", "Intranet", "--amount", "1000"])
    if link_create is not None:
        monkeypatch.setattr(stripe.PaymentLink, "create", link_create)
    return r


def _invoice_count(home):
    db = DB(home / "invoiceguard.db")
    try:
        return len(db.list_invoices())
    finally:
        db.close()


def test_failed_invoice_create_leaves_no_draft_no_key(home, monkeypatch):
    """No Stripe key configured -> ValueError path. Before 0.8.4 each
    failure left an orphan draft (3 failures = 3 drafts)."""
    r = _seed_cli(home, monkeypatch)
    monkeypatch.delenv("INVOICEGUARD_STRIPE_SECRET_KEY", raising=False)
    for _ in range(3):
        res = r.invoke(cli, ["invoice", "create", "--project", "1",
                             "--kind", "deposit"])
        assert res.exit_code != 0
    assert _invoice_count(home) == 0


def test_failed_invoice_create_leaves_no_draft_api_error(home, monkeypatch):
    """A live Stripe API failure (StripeError, not ValueError) must
    roll back the draft too, with a clean CLI error — not a traceback
    plus an orphan."""

    def boom(**kw):
        raise stripe.error.APIError("stripe is down")

    r = _seed_cli(home, monkeypatch, link_create=boom)
    monkeypatch.setenv("INVOICEGUARD_STRIPE_SECRET_KEY", "sk_test_000")
    res = r.invoke(cli, ["invoice", "create", "--project", "1",
                         "--kind", "deposit"])
    assert res.exit_code != 0
    assert "stripe is down" in res.output
    assert _invoice_count(home) == 0


def test_successful_create_after_failure_still_works(home, monkeypatch):
    state = {"fail": True}

    def flaky(**kw):
        if state["fail"]:
            raise stripe.error.APIError("boom")
        return types.SimpleNamespace(url="https://buy.stripe.com/x",
                                     id="plink_ok")

    r = _seed_cli(home, monkeypatch, link_create=flaky)
    monkeypatch.setenv("INVOICEGUARD_STRIPE_SECRET_KEY", "sk_test_000")
    assert r.invoke(cli, ["invoice", "create", "--project", "1",
                          "--kind", "deposit"]).exit_code != 0
    state["fail"] = False
    res = r.invoke(cli, ["invoice", "create", "--project", "1",
                         "--kind", "deposit"])
    assert res.exit_code == 0, res.output
    assert _invoice_count(home) == 1


# -- sign-status: ack and signature tracks reconciled -------------------

def test_sign_status_reports_deposit_link_ack(home):
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    res = r.invoke(cli, ["project", "create", "--client", "Hooli",
                         "--title", "T", "--amount", "500", "--ack"])
    assert res.exit_code == 0, res.output
    out = r.invoke(cli, ["project", "sign-status", "1"]).output
    # Before 0.8.4 this said only "no signature request yet" while the
    # project was already acknowledged.
    assert "acknowledged via deposit link" in out


def test_sign_status_pending_signature_shows_ack(home):
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    r.invoke(cli, ["project", "create", "--client", "Hooli",
                   "--title", "T", "--amount", "500"])
    assert r.invoke(cli, ["project", "sign-request", "1"]).exit_code == 0
    out = r.invoke(cli, ["project", "sign-status", "1"]).output
    assert "awaiting signature" in out
    assert "contract acknowledged: no" in out
    assert r.invoke(cli, ["project", "ack", "1"]).exit_code == 0
    out = r.invoke(cli, ["project", "sign-status", "1"]).output
    assert "awaiting signature" in out
    assert "contract acknowledged: yes" in out


# -- email plain-text cleanup --------------------------------------------

def test_to_plain_text_strips_subject_line_and_bold():
    body = ("Subject: Final notice: X invoice 20 days overdue\n"
            "\n"
            "Hi Acme,\n\nRemaining balance: **$1,250.00**.\n")
    out = to_plain_text(body)
    assert "Subject:" not in out
    assert out.startswith("Hi Acme,")
    assert "$1,250.00" in out
    assert "**" not in out


def test_to_plain_text_keeps_normal_body():
    body = "Hi Acme,\n\nYour invoice is due.\n"
    assert to_plain_text(body) == body


def test_dunning_email_body_is_clean(db, sample_project, smtp_cfg, home,
                                     monkeypatch):
    """End to end through check_due: the body handed to the mailer has
    no Subject: line and no literal markdown asterisks."""
    from datetime import date, timedelta

    from invoiceguard.dunning import check_due

    bodies = []
    monkeypatch.setattr(
        "invoiceguard.dunning.send_email",
        lambda cfg, to, subject, body: bodies.append(body))
    due = (date.today() - timedelta(days=2)).isoformat()
    iid = db.add_invoice(sample_project["id"], "deposit", 125_000, "USD",
                         status="sent", due_date=due)
    db.update_invoice(iid, stripe_url="https://pay.stripe.test/x")
    results = check_due(db, smtp_cfg)
    assert [x["stage"] for x in results] == ["day1"]
    assert bodies, "expected one dunning email"
    body = bodies[0]
    assert "Subject:" not in body
    assert "**" not in body
    assert "$1,250.00" in body
