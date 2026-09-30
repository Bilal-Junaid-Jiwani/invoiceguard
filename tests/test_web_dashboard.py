"""Dashboard integration tests — run against the integrated package.

Covers the FastAPI dashboard (invoiceguard.web.main):
  - page rendering (index, detail, 404s)
  - Stripe webhook verification (real HMAC via stripe.Webhook.construct_event,
    offline) — the same checks as the standalone test_webhook.py script
  - regression: the canonical metadata key 'invoiceguard_invoice_id'
    (written by CLI create_payment_link) must mark the invoice paid

All from actual runs; no network involved.
"""

from __future__ import annotations

import json
import os
import sqlite3

import pytest
import stripe
from fastapi.testclient import TestClient

TEST_SECRET = "whsec_test_qa_integration"


def _seed(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    now = "2026-09-30T10:00:00+00:00"
    c.execute(
        "INSERT INTO clients(name, email, created_at) VALUES (?,?,?)",
        ("Test Client", "billing@example.com", now),
    )
    cid = c.lastrowid
    c.execute(
        """INSERT INTO projects(client_id, title, amount_cents, currency,
               deposit_pct, late_fee_pct, late_fee_grace_days,
               contract_md, contract_ack, contract_ack_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, "Test Project", 200000, "USD", 50.0, 1.5, 15,
         "# contract", 1, now, now),
    )
    pid = c.lastrowid

    def inv(kind, cents, status, due, session_id):
        c.execute(
            """INSERT INTO invoices(project_id, kind, amount_cents, currency,
                   status, stripe_session_id, due_date, sent_at, paid_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (pid, kind, cents, "USD", status, session_id, due,
             "2026-09-20T09:00:00+00:00", None, now),
        )
        return c.lastrowid

    sent_id = inv("deposit", 100000, "sent", "2026-10-07", "cs_test_meta_canonical")
    inv("milestone", 50000, "sent", "2026-10-07", "cs_test_meta_legacy")
    conn.commit()
    conn.close()
    return {"sent_id": sent_id, "db": db_path}


def _signed(secret: str, payload: dict, tamper: bool = False) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    header = stripe.WebhookSignature.generate_signature_header(body.decode(), secret)
    if tamper:
        header = header.replace("v1=", "v1=deadbeef", 1)
    return body, header


def _event(session_id: str, etype: str = "checkout.session.completed",
           metadata: dict | None = None) -> dict:
    session = {"id": session_id, "object": "checkout.session",
               "metadata": metadata or {}}
    return {"id": "evt_test_1", "object": "event", "type": etype,
            "data": {"object": session}}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_SECRET)
    from invoiceguard.web import db as wdb
    from invoiceguard.web.main import app
    wdb.init_db()  # create tables before seeding (lifespan runs later)
    seeded = _seed(db)
    with TestClient(app) as tc:
        yield tc, seeded, db


def _status(db_path, invoice_id):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status, paid_at FROM invoices WHERE id = ?",
                       (invoice_id,)).fetchone()
    conn.close()
    return row


def post(client, payload, secret=TEST_SECRET, tamper=False):
    body, header = _signed(secret, payload, tamper)
    return client.post("/webhooks/stripe", content=body,
                       headers={"Content-Type": "application/json",
                                "Stripe-Signature": header})


# -- pages ---------------------------------------------------------------
def test_index_renders(client):
    tc, seeded, _ = client
    r = tc.get("/")
    assert r.status_code == 200
    assert "InvoiceGuard" in r.text
    assert "Test Client" in r.text


def test_index_status_filter(client):
    tc, _, _ = client
    assert tc.get("/?status=sent").status_code == 200
    assert tc.get("/?status=bogus").status_code == 404


def test_detail_renders(client):
    tc, seeded, _ = client
    r = tc.get(f"/invoices/{seeded['sent_id']}")
    assert r.status_code == 200
    assert "Test Project" in r.text
    assert tc.get("/invoices/99999").status_code == 404


def test_healthz(client):
    tc, _, _ = client
    assert tc.get("/healthz").json() == {"ok": True}


# -- webhook: the 7 offline checks ----------------------------------------
def test_webhook_canonical_metadata_key_marks_paid(client):
    """Regression: CLI writes metadata key 'invoiceguard_invoice_id';
    the dashboard webhook must honor it (not only 'invoice_id')."""
    tc, seeded, db = client
    iid = seeded["sent_id"]
    r = post(tc, _event("cs_test_meta_canonical",
                        metadata={"invoiceguard_invoice_id": str(iid)}))
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] == iid
    status, paid_at = _status(db, iid)
    assert status == "paid" and paid_at is not None


def test_webhook_legacy_invoice_id_key_still_works(client):
    tc, seeded, db = client
    iid = seeded["sent_id"] + 1
    r = post(tc, _event("cs_test_meta_legacy",
                        metadata={"invoice_id": str(iid)}))
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] == iid
    assert _status(db, iid)[0] == "paid"


def test_webhook_tampered_signature_rejected(client):
    tc, _, _ = client
    r = post(tc, _event("cs_test_meta_canonical"), tamper=True)
    assert r.status_code == 400


def test_webhook_unknown_event_ignored(client):
    tc, _, _ = client
    r = post(tc, _event("cs_test_meta_canonical", "payment_intent.created"))
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] is None


def test_webhook_unknown_session_ignored(client):
    tc, _, _ = client
    r = post(tc, _event("cs_test_nope"))
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] is None


def test_webhook_idempotent_when_already_paid(client):
    tc, seeded, db = client
    iid = seeded["sent_id"]
    post(tc, _event("cs_test_meta_canonical",
                    metadata={"invoiceguard_invoice_id": str(iid)}))
    first_paid_at = _status(db, iid)[1]
    r = post(tc, _event("cs_test_meta_canonical",
                        metadata={"invoiceguard_invoice_id": str(iid)}))
    assert r.status_code == 200
    assert _status(db, iid)[1] == first_paid_at  # not re-marked


def test_webhook_no_secret_configured(tmp_path, monkeypatch):
    db = str(tmp_path / "web2.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    from invoiceguard.web.main import app
    with TestClient(app) as tc:
        body, header = _signed("whsec_x", _event("cs_x"))
        r = tc.post("/webhooks/stripe", content=body,
                    headers={"Content-Type": "application/json",
                             "Stripe-Signature": header})
    assert r.status_code == 500
    assert "not configured" in r.text
