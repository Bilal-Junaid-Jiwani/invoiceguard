"""0.41.0 — stripe_session_id holds checkout session ids only.

Audit Bug 2: `invoice create` stored the Stripe Payment Link id
(plink_...) in invoices.stripe_session_id, while the web webhook's
fallback lookup compares that column against checkout SESSION ids
(cs_...) — the fallback could never match a CLI-created invoice. The
link id now has its own column (invoices.stripe_link_id, additive
migration), the CLI writes it there, and the fallback only ever
matches cs_ values (databases in the wild still carry plink_ values
in stripe_session_id from older CLIs).
"""

from __future__ import annotations

import json
import sqlite3
import types

import pytest
import stripe
from click.testing import CliRunner
from fastapi.testclient import TestClient

from invoiceguard.cli import cli
from invoiceguard.db import DB

TEST_SECRET = "whsec_test_link_id"


def test_invoice_create_stores_link_id_in_its_own_column(
        home, monkeypatch):
    def fake_create(**kwargs):
        return types.SimpleNamespace(
            url="https://buy.stripe.com/test_fake123",
            id="plink_test_fake123",
        )

    monkeypatch.setattr(stripe.PaymentLink, "create", fake_create)
    monkeypatch.setenv("INVOICEGUARD_STRIPE_SECRET_KEY", "sk_test_000")
    r = CliRunner()
    assert r.invoke(cli, ["client", "add", "--name", "Hooli"]).exit_code == 0
    assert r.invoke(cli, ["project", "create", "--client", "Hooli",
                          "--title", "Intranet",
                          "--amount", "1000"]).exit_code == 0
    res = r.invoke(cli, ["invoice", "create", "--project", "1",
                         "--kind", "deposit"])
    assert res.exit_code == 0, res.output

    db = DB(home / "invoiceguard.db")
    try:
        inv = db.list_invoices()[0]
    finally:
        db.close()
    assert inv["stripe_link_id"] == "plink_test_fake123"
    assert inv["stripe_session_id"] is None
    assert inv["stripe_url"] == "https://buy.stripe.com/test_fake123"
    assert inv["status"] == "sent"


# -- webhook session-id fallback ----------------------------------------


def _seed_invoice(db_path: str, stored_ref: str | None) -> int:
    conn = sqlite3.connect(db_path)
    now = "2026-09-30T10:00:00+00:00"
    conn.execute(
        "INSERT INTO clients(name, email, created_at) VALUES (?,?,?)",
        ("Test Client", "billing@example.com", now))
    conn.execute(
        """INSERT INTO projects(client_id, title, amount_cents, currency,
               deposit_pct, late_fee_pct, late_fee_grace_days,
               contract_md, contract_ack, contract_ack_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (1, "Test Project", 200_000, "USD", 50.0, 1.5, 15,
         "# contract", 1, now, now))
    cur = conn.execute(
        """INSERT INTO invoices(project_id, kind, amount_cents, currency,
               status, stripe_session_id, due_date, sent_at, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (1, "deposit", 75_000, "USD", "sent", stored_ref, "2026-10-07",
         "2026-09-20T09:00:00+00:00", now))
    conn.commit()
    iid = cur.lastrowid
    conn.close()
    return iid


def _post_session(tc, event_id: str, session_id: str):
    """A completed-session event with NO metadata hint — the fallback
    lookup is the only way it can be matched to an invoice."""
    payload = {"id": event_id, "object": "event",
               "type": "checkout.session.completed",
               "data": {"object": {
                   "id": session_id, "object": "checkout.session",
                   "metadata": {}, "amount_total": 75_000}}}
    body = json.dumps(payload).encode()
    header = stripe.WebhookSignature.generate_signature_header(
        body.decode(), TEST_SECRET)
    return tc.post("/webhooks/stripe", content=body,
                   headers={"Content-Type": "application/json",
                            "Stripe-Signature": header})


@pytest.fixture()
def web_client(tmp_path, monkeypatch):
    db = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_SECRET)
    from invoiceguard.web import db as wdb
    from invoiceguard.web.main import app
    wdb.init_db()
    with TestClient(app) as tc:
        yield tc, db


def test_fallback_matches_a_recorded_session_id(web_client):
    tc, db = web_client
    iid = _seed_invoice(db, "cs_recorded_1")
    r = _post_session(tc, "evt_fb_1", "cs_recorded_1")
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] == iid


def test_fallback_never_matches_a_stored_plink(web_client):
    """An invoice a pre-0.41.0 CLI left behind carries plink_... in
    stripe_session_id; a metadata-less session event must NOT be
    matched to it through the session-id fallback."""
    tc, db = web_client
    _seed_invoice(db, "plink_legacy_1")
    r = _post_session(tc, "evt_fb_2", "cs_brand_new_1")
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] is None
