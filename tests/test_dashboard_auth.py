"""0.41.0 — dashboard token authentication.

Finding 2 of the 2026-11-23 security audit: the web app had no
authentication anywhere, so anyone who could reach it (the documented
webhook/signing deployment exposes it) could read every client's
name/email, amounts and payment history. The data pages (GET / and
GET /invoices/{id}) now require the dashboard token when one is
configured (INVOICEGUARD_DASHBOARD_TOKEN env, or the dashboard_token
config key): Bearer header, the cookie set by a ?token= visit, or
?token= itself. /webhooks/stripe (Stripe-signature verified),
/sign/{token} (capability URL) and /healthz stay open. With NO token
configured the dashboard stays open for trusted-localhost use — and
startup warns loudly that it is unauthenticated.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest
import stripe
from fastapi.testclient import TestClient

TEST_SECRET = "whsec_test_dash_auth"
DASH_TOKEN = "test-dashboard-token-0123456789"


def _seed(db_path: str) -> dict:
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
         "# contract", 0, None, now),
    )
    pid = conn.execute("SELECT id FROM projects").fetchone()[0]
    cur = conn.execute(
        """INSERT INTO invoices(project_id, kind, amount_cents, currency,
               status, due_date, sent_at, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (pid, "deposit", 100_000, "USD", "sent", "2026-10-07",
         "2026-09-20T09:00:00+00:00", now),
    )
    iid = cur.lastrowid
    conn.execute(
        """INSERT INTO signatures(project_id, token, status, contract_md,
               created_at) VALUES (?,?,?,?,?)""",
        (pid, "tok_auth_test", "pending", "# contract", now),
    )
    conn.commit()
    conn.close()
    return {"invoice_id": iid, "sign_token": "tok_auth_test"}


@pytest.fixture()
def authed_client(tmp_path, monkeypatch):
    """TestClient with a dashboard token configured via env."""
    db = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_SECRET)
    monkeypatch.setenv("INVOICEGUARD_DASHBOARD_TOKEN", DASH_TOKEN)
    # an empty config dir, so no stray dashboard_token can interfere
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("INVOICEGUARD_CONFIG", str(cfg_dir / "config.yaml"))
    from invoiceguard.web import db as wdb
    from invoiceguard.web.main import app
    wdb.init_db()
    seeded = _seed(db)
    with TestClient(app) as tc:
        yield tc, seeded, db


def test_dashboard_pages_401_without_token(authed_client):
    tc, seeded, _ = authed_client
    assert tc.get("/").status_code == 401
    assert tc.get(f"/invoices/{seeded['invoice_id']}").status_code == 401


def test_dashboard_pages_401_with_wrong_token(authed_client):
    tc, seeded, _ = authed_client
    r = tc.get("/", headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401
    r = tc.get(f"/?token=wrong-token")
    assert r.status_code == 401


def test_dashboard_pages_200_with_bearer_token(authed_client):
    tc, seeded, _ = authed_client
    headers = {"Authorization": f"Bearer {DASH_TOKEN}"}
    r = tc.get("/", headers=headers)
    assert r.status_code == 200
    assert "Test Client" in r.text
    r = tc.get(f"/invoices/{seeded['invoice_id']}", headers=headers)
    assert r.status_code == 200
    assert "Test Project" in r.text


def test_dashboard_query_token_sets_cookie_for_links(authed_client):
    tc, seeded, _ = authed_client
    r = tc.get(f"/?token={DASH_TOKEN}")
    assert r.status_code == 200
    assert "invoiceguard_dashboard_token" in r.headers.get("set-cookie", "")
    # the cookie now carries the auth, so in-dashboard links (which
    # cannot set headers) keep working without the query param
    assert tc.get("/").status_code == 200
    assert tc.get(f"/invoices/{seeded['invoice_id']}").status_code == 200


def test_webhook_unaffected_by_dashboard_token(authed_client):
    """Stripe cannot present a dashboard token; the webhook keeps its
    own signature verification and stays reachable without one."""
    tc, seeded, db = authed_client
    iid = seeded["invoice_id"]
    payload = {"id": "evt_auth_1", "object": "event",
               "type": "checkout.session.completed",
               "data": {"object": {
                   "id": "cs_auth_1", "object": "checkout.session",
                   "metadata": {"invoiceguard_invoice_id": str(iid)},
                   "amount_total": 100_000}}}
    body = json.dumps(payload).encode()
    header = stripe.WebhookSignature.generate_signature_header(
        body.decode(), TEST_SECRET)
    r = tc.post("/webhooks/stripe", content=body,
                headers={"Content-Type": "application/json",
                         "Stripe-Signature": header})
    assert r.status_code == 200
    assert r.json()["marked_invoice_id"] == iid


def test_sign_page_and_healthz_unaffected(authed_client):
    tc, seeded, _ = authed_client
    assert tc.get("/healthz").json() == {"ok": True}
    r = tc.get(f"/sign/{seeded['sign_token']}")
    assert r.status_code == 200


def test_dashboard_token_from_config_file(tmp_path, monkeypatch):
    db = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.delenv("INVOICEGUARD_DASHBOARD_TOKEN", raising=False)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.yaml").write_text(
        "dashboard_token: cfg-dashboard-token-42\n", encoding="utf-8")
    monkeypatch.setenv("INVOICEGUARD_CONFIG", str(cfg_dir / "config.yaml"))
    from invoiceguard.web import db as wdb
    from invoiceguard.web.main import app
    wdb.init_db()
    _seed(db)
    with TestClient(app) as tc:
        assert tc.get("/").status_code == 401
        r = tc.get("/", headers={
            "Authorization": "Bearer cfg-dashboard-token-42"})
        assert r.status_code == 200


def test_no_token_configured_open_dashboard_but_startup_warns(
        tmp_path, monkeypatch, caplog):
    db = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db)
    monkeypatch.delenv("INVOICEGUARD_DASHBOARD_TOKEN", raising=False)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    monkeypatch.setenv("INVOICEGUARD_CONFIG", str(cfg_dir / "config.yaml"))
    from invoiceguard.web import db as wdb
    from invoiceguard.web.main import app
    wdb.init_db()
    _seed(db)
    with caplog.at_level(logging.WARNING, logger="invoiceguard.web.main"):
        with TestClient(app) as tc:
            # trusted-localhost mode: still open...
            assert tc.get("/").status_code == 200
    # ...but never silent about it
    assert "dashboard authentication is NOT configured" in caplog.text
