"""Webhook tests — REAL signature verification, offline.

stripe.Webhook.construct_event does genuine HMAC verification against a
test secret; no network is involved.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
import stripe

from invoiceguard.db import DB
from invoiceguard.webhooks import handle_event, verify_event

TEST_SECRET = "whsec_test_0123456789abcdef"


def sign(payload: bytes, secret: str = TEST_SECRET) -> str:
    ts = int(time.time())
    sig = hmac.new(
        secret.encode(),
        f"{ts}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={ts},v1={sig}"


def make_event(invoice_id: int) -> bytes:
    return json.dumps({
        "id": "evt_test_123",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_test_123",
            "metadata": {"invoiceguard_invoice_id": str(invoice_id)},
        }},
    }).encode()


def test_verify_event_real_signature():
    payload = make_event(5)
    event = verify_event(payload, sign(payload), TEST_SECRET)
    assert event["type"] == "checkout.session.completed"


def test_verify_event_tampered_payload_rejected():
    payload = make_event(5)
    header = sign(payload)
    tampered = payload.replace(b"cs_test_123", b"cs_test_999")
    with pytest.raises(stripe.error.SignatureVerificationError):
        verify_event(tampered, header, TEST_SECRET)


def test_verify_event_wrong_secret_rejected():
    payload = make_event(5)
    with pytest.raises(stripe.error.SignatureVerificationError):
        verify_event(payload, sign(payload, secret="whsec_wrong"), TEST_SECRET)


def test_verify_event_placeholder_secret_rejected():
    with pytest.raises(ValueError, match="not configured"):
        verify_event(b"{}", "t=1,v1=abc", "whsec_...")


def test_handle_event_marks_invoice_paid(db, sample_project):
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000, "USD",
                         status="sent")
    action = handle_event(db, json.loads(make_event(iid).decode()))
    assert action == "invoice_paid"
    inv = db.get_invoice(iid)
    assert inv["status"] == "paid"
    assert inv["paid_at"] is not None
    assert inv["stripe_session_id"] == "cs_test_123"


def test_handle_event_idempotent_on_replay(db, sample_project):
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000, "USD",
                         status="sent")
    evt = json.loads(make_event(iid).decode())
    assert handle_event(db, evt) == "invoice_paid"
    assert handle_event(db, evt) == "ignored:already_paid"


def test_handle_event_unknown_type_ignored(db, sample_project):
    iid = db.add_invoice(sample_project["id"], "deposit", 100_000, "USD",
                         status="sent")
    action = handle_event(db, {"type": "invoice.finalized",
                               "data": {"object": {}}})
    assert action.startswith("ignored:")
    assert db.get_invoice(iid)["status"] == "sent"
