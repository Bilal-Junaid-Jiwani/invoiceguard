"""0.41.0 regression tests — the served Stripe webhook is idempotent.

Finding 1 of the 2026-11-23 security audit: POST /webhooks/stripe had
no event-id dedupe and mark_invoice_paid had no session dedupe, so a
redelivered partial-payment event recorded the payment again on every
retry until the invoice flipped to fully paid on a fraction of the
money (the standalone handler in webhooks.py already deduped; the
served path did not). These tests pin the fixed behavior end-to-end
through the real FastAPI app (locally signed payloads, offline) and at
the web-db layer directly.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
import stripe
from fastapi.testclient import TestClient

TEST_SECRET = "whsec_test_idempotency"


def _seed(db_path: str, amount_cents: int = 10_000) -> int:
    """One client/project and one `sent` invoice; returns invoice id."""
    conn = sqlite3.connect(db_path)
    now = "2026-09-30T10:00:00+00:00"
    conn.execute(
        "INSERT INTO clients(name, email, created_at) VALUES (?,?,?)",
        ("Test Client", "billing@example.com", now),
    )
    cid = conn.execute("SELECT id FROM clients").fetchone()[0]
    conn.execute(
        """INSERT INTO projects(client_id, title, amount_cents, currency,
               deposit_pct, late_fee_pct, late_fee_grace_days,
               contract_md, contract_ack, contract_ack_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (cid, "Test Project", 200_000, "USD", 50.0, 1.5, 15,
         "# contract", 1, now, now),
    )
    pid = conn.execute("SELECT id FROM projects").fetchone()[0]
    cur = conn.execute(
        """INSERT INTO invoices(project_id, kind, amount_cents, currency,
               status, due_date, sent_at, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (pid, "deposit", amount_cents, "USD", "sent", "2026-10-07",
         "2026-09-20T09:00:00+00:00", now),
    )
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid


def _event(event_id: str, session_id: str, invoice_id: int,
           amount_total: int, etype: str = "checkout.session.completed",
           payment_status: str | None = None) -> dict:
    session = {"id": session_id, "object": "checkout.session",
               "metadata": {"invoiceguard_invoice_id": str(invoice_id)},
               "amount_total": amount_total}
    if payment_status is not None:
        session["payment_status"] = payment_status
    return {"id": event_id, "object": "event", "type": etype,
            "data": {"object": session}}


def _post(tc, payload: dict):
    body = json.dumps(payload).encode()
    header = stripe.WebhookSignature.generate_signature_header(
        body.decode(), TEST_SECRET)
    return tc.post("/webhooks/stripe", content=body,
                   headers={"Content-Type": "application/json",
                            "Stripe-Signature": header})


def _payments(db_path: str, invoice_id: int) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT amount_cents, note FROM payments WHERE invoice_id = ? "
        "ORDER BY id", (invoice_id,)).fetchall()
    conn.close()
    return rows


def _status(db_path: str, invoice_id: int) -> str:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM invoices WHERE id = ?",
                       (invoice_id,)).fetchone()
    conn.close()
    return row[0]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_SECRET)
    from invoiceguard.web import db as wdb
    from invoiceguard.web.main import app
    wdb.init_db()
    iid = _seed(db)
    with TestClient(app) as tc:
        yield tc, iid, db


def test_redelivered_partial_payment_event_records_once(client):
    """The audit's repro: one $40 session against a $100 invoice,
    delivered three times (Stripe at-least-once retries, same event
    id). Before 0.41.0 the ledger went $40 -> $80 -> $100 and the
    invoice flipped to paid; now the payment is recorded exactly once.
    """
    tc, iid, db = client
    evt = _event("evt_replay_1", "cs_replay_1", iid, 4_000)

    r1 = _post(tc, evt)
    assert r1.status_code == 200
    assert r1.json()["marked_invoice_id"] == iid

    for _ in range(2):
        r = _post(tc, evt)
        assert r.status_code == 200
        assert r.json()["duplicate"] is True
        assert r.json()["marked_invoice_id"] == iid

    pays = _payments(db, iid)
    assert len(pays) == 1
    assert pays[0][0] == 4_000
    assert _status(db, iid) == "partially-paid"


def test_distinct_events_same_session_record_once(client):
    """The audit's second variant: checkout.session.completed with no
    payment_status (treated as paid for backward compatibility) and
    the follow-up checkout.session.async_payment_succeeded are two
    DISTINCT events (distinct ids) for the SAME session — event-id
    dedupe alone cannot stop the double record; the session-level
    dedupe in mark_invoice_paid does."""
    tc, iid, db = client
    r1 = _post(tc, _event("evt_sess_a", "cs_sess_1", iid, 4_000))
    assert r1.json()["marked_invoice_id"] == iid
    r2 = _post(tc, _event("evt_sess_b", "cs_sess_1", iid, 4_000,
                          etype="checkout.session.async_payment_succeeded",
                          payment_status="paid"))
    assert r2.status_code == 200
    assert r2.json()["marked_invoice_id"] == iid
    pays = _payments(db, iid)
    assert len(pays) == 1
    assert pays[0][0] == 4_000
    assert _status(db, iid) == "partially-paid"


def test_mark_invoice_paid_same_session_redelivered_directly(
        tmp_path, monkeypatch):
    """Defense in depth at the ledger layer: calling mark_invoice_paid
    twice with the same session id inserts one payment row; a second,
    different session can still pay the rest of the invoice."""
    db_file = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db_file)
    from invoiceguard.web import db as wdb
    wdb.init_db()
    iid = _seed(db_file)

    assert wdb.mark_invoice_paid(
        "cs_direct", invoice_id_hint=iid, amount_cents=4_000) == iid
    assert wdb.mark_invoice_paid(
        "cs_direct", invoice_id_hint=iid, amount_cents=4_000) == iid
    pays = wdb.payments(iid)
    assert len(pays) == 1
    assert pays[0]["amount_cents"] == 4_000
    assert wdb.get_invoice(iid)["status"] == "partially-paid"

    assert wdb.mark_invoice_paid(
        "cs_direct_2", invoice_id_hint=iid, amount_cents=6_000) == iid
    assert len(wdb.payments(iid)) == 2
    assert wdb.get_invoice(iid)["status"] == "paid"
