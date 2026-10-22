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
           metadata: dict | None = None,
           payment_status: str | None = None,
           amount_total: int | None = None) -> dict:
    session = {"id": session_id, "object": "checkout.session",
               "metadata": metadata or {}}
    if payment_status is not None:
        session["payment_status"] = payment_status
    if amount_total is not None:
        session["amount_total"] = amount_total
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


def _seed_plink_invoice(db_path, session_ref="plink_test_999"):
    """Insert an invoice the way the CLI leaves it: stripe_session_id
    holds the Payment Link id (plink_...), NOT a checkout session id."""
    conn = sqlite3.connect(db_path)
    pid = conn.execute("SELECT id FROM projects LIMIT 1").fetchone()[0]
    cur = conn.execute(
        """INSERT INTO invoices(project_id, kind, amount_cents, currency,
               status, stripe_session_id, due_date, sent_at, paid_at,
               created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (pid, "deposit", 75000, "USD", "sent", session_ref, "2026-10-07",
         "2026-09-20T09:00:00+00:00", None, "2026-09-20T09:00:00+00:00"),
    )
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid


def test_webhook_records_payment_for_plink_invoice(client):
    """THE 0.8.4 regression (external review, reproduced live with
    stripe 15.6.1): in production the CLI stores the payment-link id
    (plink_...) in stripe_session_id while the webhook carries the
    checkout session id (cs_...) — the session-id fallback can NEVER
    match. The only bridge is the metadata invoice id, but the old
    handler dropped it under stripe>=15 because StripeObject metadata
    fails isinstance(metadata, dict). The older tests missed it
    because they seeded stripe_session_id with the cs_ id itself.
    """
    tc, seeded, db = client
    iid = _seed_plink_invoice(db)
    r = post(tc, _event("cs_test_live_999",
                        metadata={"invoiceguard_invoice_id": str(iid)},
                        payment_status="paid", amount_total=75000))
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] == iid
    status, paid_at = _status(db, iid)
    assert status == "paid" and paid_at is not None
    conn = sqlite3.connect(db)
    sid = conn.execute("SELECT stripe_session_id FROM invoices "
                       "WHERE id = ?", (iid,)).fetchone()[0]
    pays = conn.execute("SELECT COUNT(*) FROM payments "
                        "WHERE invoice_id = ?", (iid,)).fetchone()[0]
    conn.close()
    assert sid == "cs_test_live_999"  # session id recorded on payment
    assert pays == 1


def test_webhook_unpaid_payment_status_records_nothing(client):
    """checkout.session.completed with payment_status=unpaid (delayed
    methods like ACH) must NOT mark the invoice paid — the money has
    not arrived. The async_payment_succeeded follow-up does."""
    tc, seeded, db = client
    iid = _seed_plink_invoice(db, "plink_test_ach")
    r = post(tc, _event("cs_test_ach_1",
                        metadata={"invoiceguard_invoice_id": str(iid)},
                        payment_status="unpaid", amount_total=75000))
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] is None
    assert _status(db, iid)[0] == "sent"
    r = post(tc, _event("cs_test_ach_1",
                        etype="checkout.session.async_payment_succeeded",
                        metadata={"invoiceguard_invoice_id": str(iid)},
                        payment_status="paid", amount_total=75000))
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] == iid
    assert _status(db, iid)[0] == "paid"


def test_webhook_no_secret_configured(tmp_path, monkeypatch):
    db = str(tmp_path / "web2.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("INVOICEGUARD_STRIPE_WEBHOOK_SECRET", raising=False)
    # point at an empty config dir so a stray ~/.invoiceguard/config.yaml
    # on the dev machine cannot leak a secret into this check
    cfg_dir = tmp_path / "empty_cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("INVOICEGUARD_CONFIG", str(cfg_dir / "config.yaml"))
    from invoiceguard.web.main import app
    with TestClient(app) as tc:
        body, header = _signed("whsec_x", _event("cs_x"))
        r = tc.post("/webhooks/stripe", content=body,
                    headers={"Content-Type": "application/json",
                             "Stripe-Signature": header})
    assert r.status_code == 500
    assert "not configured" in r.text


# -- webhook secret resolution: INVOICEGUARD_* env + config file ---------
# Regression for the bug where the dashboard only honored the bare
# STRIPE_WEBHOOK_SECRET env var while the README told users to set
# stripe_webhook_secret in the config (or INVOICEGUARD_STRIPE_WEBHOOK_SECRET).


def _webhook_client(tmp_path, monkeypatch, seeded_db=True):
    """TestClient for /webhooks/stripe with NO secret configured.

    Returns (test_client, db_path). Caller sets the secret source."""
    db = str(tmp_path / "web_secret.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("INVOICEGUARD_STRIPE_WEBHOOK_SECRET", raising=False)
    from invoiceguard.web import db as wdb
    from invoiceguard.web.main import app
    wdb.init_db()
    seeded = _seed(db) if seeded_db else {}
    tc = TestClient(app)
    tc.__enter__()
    return tc, seeded, db


def test_webhook_env_secret_honored(tmp_path, monkeypatch):
    tc, seeded, db = _webhook_client(tmp_path, monkeypatch)
    try:
        monkeypatch.setenv("INVOICEGUARD_STRIPE_WEBHOOK_SECRET", TEST_SECRET)
        iid = seeded["sent_id"]
        r = post(tc, _event("cs_test_meta_canonical",
                            metadata={"invoiceguard_invoice_id": str(iid)}))
        assert r.status_code == 200
        assert r.json()["marked_invoice_id"] == iid
        assert _status(db, iid)[0] == "paid"
    finally:
        tc.__exit__(None, None, None)


def test_webhook_config_file_secret_honored(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "stripe_webhook_secret: whsec_test_config_file\n", encoding="utf-8")
    monkeypatch.setenv("INVOICEGUARD_CONFIG", str(cfg_dir / "config.yaml"))
    tc, seeded, db = _webhook_client(tmp_path, monkeypatch)
    try:
        iid = seeded["sent_id"]
        body, header = _signed("whsec_test_config_file",
                               _event("cs_test_meta_canonical",
                                      metadata={"invoiceguard_invoice_id": str(iid)}))
        r = tc.post("/webhooks/stripe", content=body,
                    headers={"Content-Type": "application/json",
                             "Stripe-Signature": header})
        assert r.status_code == 200
        assert r.json()["marked_invoice_id"] == iid
        assert _status(db, iid)[0] == "paid"
    finally:
        tc.__exit__(None, None, None)


def test_webhook_prefers_invoicguard_env_over_legacy(tmp_path, monkeypatch):
    tc, seeded, db = _webhook_client(tmp_path, monkeypatch)
    try:
        monkeypatch.setenv("INVOICEGUARD_STRIPE_WEBHOOK_SECRET", TEST_SECRET)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_wrong_legacy")
        iid = seeded["sent_id"]
        r = post(tc, _event("cs_test_meta_canonical",
                            metadata={"invoiceguard_invoice_id": str(iid)}))
        assert r.status_code == 200  # 400 would mean the legacy key won
        assert _status(db, iid)[0] == "paid"
    finally:
        tc.__exit__(None, None, None)


def test_webhook_placeholder_secret_counts_as_unconfigured(tmp_path, monkeypatch):
    tc, _, _ = _webhook_client(tmp_path, monkeypatch, seeded_db=False)
    try:
        monkeypatch.setenv("INVOICEGUARD_STRIPE_WEBHOOK_SECRET", "whsec_...")
        body, header = _signed("whsec_...", _event("cs_x"))
        r = tc.post("/webhooks/stripe", content=body,
                    headers={"Content-Type": "application/json",
                             "Stripe-Signature": header})
        assert r.status_code == 500
    finally:
        tc.__exit__(None, None, None)
