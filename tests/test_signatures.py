"""E-signature tests: core DB ops, web signing flow, CLI commands."""

from __future__ import annotations

import hashlib
import os

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from invoiceguard.cli import cli


FAKE_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"


# -- core DB --------------------------------------------------------------
def test_create_request_idempotent(db, sample_project):
    t1 = db.create_signature_request(sample_project["id"])
    t2 = db.create_signature_request(sample_project["id"])
    assert t1 == t2  # one pending request per project, link stays valid
    row = db.get_signature_by_token(t1)
    assert row["status"] == "pending"
    assert row["project_id"] == sample_project["id"]


def test_create_request_unknown_project(db):
    with pytest.raises(ValueError, match="no project"):
        db.create_signature_request(999)


def test_get_signature_for_project_none(db, sample_project):
    assert db.get_signature_for_project(sample_project["id"]) is None


def test_sign_contract_happy_path(db, sample_project):
    pid = sample_project["id"]
    token = db.create_signature_request(pid)
    res = db.sign_contract(token, "Jane Client", FAKE_IMAGE)
    expected_hash = hashlib.sha256(
        sample_project["contract_md"].encode("utf-8")).hexdigest()
    assert res["contract_hash"] == expected_hash
    assert res["signer_name"] == "Jane Client"
    assert res["project_id"] == pid
    row = db.get_signature_by_token(token)
    assert row["status"] == "signed"
    assert row["signature_image"] == FAKE_IMAGE
    assert row["signed_at"] is not None
    # a signed contract IS the acknowledgment
    proj = db.get_project(pid)
    assert proj["contract_ack"] == 1
    assert proj["contract_ack_at"] == res["signed_at"]


def test_create_request_freezes_contract_snapshot(db, sample_project):
    """create_signature_request freezes the contract text at creation."""
    pid = sample_project["id"]
    token = db.create_signature_request(pid)
    row = db.get_signature_by_token(token)
    assert row["contract_md"] == sample_project["contract_md"]
    # editing the project afterwards must not move the snapshot
    db.conn.execute("UPDATE projects SET contract_md = ? WHERE id = ?",
                    ("# edited contract", pid))
    db.conn.commit()
    row = db.get_signature_by_token(token)
    assert row["contract_md"] == sample_project["contract_md"]


def test_sign_contract_hash_covers_frozen_snapshot(db, sample_project):
    """The hash must cover the frozen snapshot, not text edited after
    the link was created — this is what the client actually saw and signed."""
    pid = sample_project["id"]
    token = db.create_signature_request(pid)
    db.conn.execute("UPDATE projects SET contract_md = ? WHERE id = ?",
                    ("# edited contract", pid))
    db.conn.commit()
    res = db.sign_contract(token, "Jane", FAKE_IMAGE)
    assert res["contract_hash"] == hashlib.sha256(
        sample_project["contract_md"].encode("utf-8")).hexdigest()


def test_sign_contract_legacy_row_without_snapshot(db, sample_project):
    """Rows created before v0.8.3 have no snapshot: fall back to the
    current project text."""
    pid = sample_project["id"]
    db.conn.execute(
        "INSERT INTO signatures(project_id, token, status, created_at) "
        "VALUES (?, 'legacy-token', 'pending', ?)",
        (pid, "2026-01-01T00:00:00"))
    db.conn.commit()
    db.conn.execute("UPDATE projects SET contract_md = ? WHERE id = ?",
                    ("# edited contract", pid))
    db.conn.commit()
    res = db.sign_contract("legacy-token", "Jane", FAKE_IMAGE)
    assert res["contract_hash"] == hashlib.sha256(
        b"# edited contract").hexdigest()


def test_migrate_signatures_contract_md_adds_column(tmp_path):
    """Old databases (no contract_md column) get it idempotently."""
    import sqlite3
    p = tmp_path / "old.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE signatures(id INTEGER PRIMARY KEY, "
        "project_id INTEGER, token TEXT, status TEXT, "
        "signer_name TEXT, signature_image TEXT, contract_hash TEXT, "
        "signed_at TEXT, created_at TEXT);")
    conn.commit()
    from invoiceguard.db import migrate_signatures_contract_md
    migrate_signatures_contract_md(conn)
    migrate_signatures_contract_md(conn)  # idempotent
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signatures)")}
    assert "contract_md" in cols
    conn.close()


def test_sign_contract_single_use(db, sample_project):
    token = db.create_signature_request(sample_project["id"])
    db.sign_contract(token, "Jane", FAKE_IMAGE)
    with pytest.raises(ValueError, match="already used"):
        db.sign_contract(token, "Jane", FAKE_IMAGE)


def test_sign_contract_bad_input(db, sample_project):
    token = db.create_signature_request(sample_project["id"])
    with pytest.raises(ValueError, match="not found"):
        db.sign_contract("bogus-token", "Jane", FAKE_IMAGE)
    with pytest.raises(ValueError, match="signer name"):
        db.sign_contract(token, "   ", FAKE_IMAGE)
    with pytest.raises(ValueError, match="drawn signature"):
        db.sign_contract(token, "Jane", "not-a-data-url")
    with pytest.raises(ValueError, match="too large"):
        db.sign_contract(token, "Jane", "data:image/png;base64," + "A" * 1_000_000)


# -- web flow -------------------------------------------------------------
@pytest.fixture()
def web_client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db_path)
    from invoiceguard.web import db as wdb
    from invoiceguard.web.main import app
    wdb.init_db()
    with TestClient(app) as tc:
        yield tc


def _make_request(web_client, monkeypatch=None):
    from invoiceguard.db import DB
    core = DB(os.environ["INVOICEGUARD_DB"])
    cid = core.add_client("Acme Corp", "billing@acme.test")
    pid = core.add_project(cid, "Website redesign", 200_000, "USD",
                           50.0, 1.5, 15, "# Freelance Services Agreement")
    token = core.create_signature_request(pid)
    core.close()
    return pid, token


def test_sign_page_renders_frozen_snapshot(web_client):
    """After the contract text is edited, the signing page still shows
    the snapshot frozen at link-creation time."""
    import hashlib as _hashlib
    pid, token = _make_request(web_client)
    from invoiceguard.db import DB
    core = DB(os.environ["INVOICEGUARD_DB"])
    core.conn.execute("UPDATE projects SET contract_md = ? WHERE id = ?",
                      ("# EDITED AFTER LINK CREATED", pid))
    core.conn.commit()
    core.close()
    r = web_client.get(f"/sign/{token}")
    assert r.status_code == 200
    assert "Freelance Services Agreement" in r.text
    assert "EDITED AFTER LINK CREATED" not in r.text
    # the web sign path hashes the same frozen snapshot
    r = web_client.post(f"/sign/{token}",
                        data={"signer_name": "Jane Client",
                              "signature_image": FAKE_IMAGE})
    assert r.status_code == 200
    core = DB(os.environ["INVOICEGUARD_DB"])
    row = core.get_signature_by_token(token)
    assert row["contract_hash"] == _hashlib.sha256(
        b"# Freelance Services Agreement").hexdigest()
    core.close()


def test_sign_page_unknown_token(web_client):
    r = web_client.get("/sign/nope")
    assert r.status_code == 404


def test_sign_page_renders_contract(web_client):
    _, token = _make_request(web_client)
    r = web_client.get(f"/sign/{token}")
    assert r.status_code == 200
    assert "Freelance Services Agreement" in r.text
    assert "sig-pad" in r.text  # drawing canvas
    assert "signer_name" in r.text


def test_sign_flow_full(web_client):
    pid, token = _make_request(web_client)
    r = web_client.post(f"/sign/{token}",
                        data={"signer_name": "Jane Client",
                              "signature_image": FAKE_IMAGE})
    assert r.status_code == 200
    assert "Contract signed" in r.text
    assert "Jane Client" in r.text

    # persisted: signed row + project acknowledged
    from invoiceguard.db import DB
    core = DB(os.environ["INVOICEGUARD_DB"])
    row = core.get_signature_by_token(token)
    assert row["status"] == "signed"
    assert row["signer_name"] == "Jane Client"
    assert core.get_project(pid)["contract_ack"] == 1
    core.close()

    # single-use: page shows already-signed, POST is rejected
    r = web_client.get(f"/sign/{token}")
    assert r.status_code == 200
    assert "already signed" in r.text
    r = web_client.post(f"/sign/{token}",
                        data={"signer_name": "Eve",
                              "signature_image": FAKE_IMAGE})
    assert r.status_code == 400


def test_sign_post_validation(web_client):
    _, token = _make_request(web_client)
    r = web_client.post(f"/sign/{token}",
                        data={"signer_name": "", "signature_image": FAKE_IMAGE})
    assert r.status_code == 400
    r = web_client.post(f"/sign/{token}",
                        data={"signer_name": "Jane", "signature_image": ""})
    assert r.status_code == 400
    r = web_client.post("/sign/nope",
                        data={"signer_name": "Jane",
                              "signature_image": FAKE_IMAGE})
    assert r.status_code == 400  # unknown token, not a crash


# -- CLI ------------------------------------------------------------------
def _cli_seed(home):
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    r.invoke(cli, ["project", "create", "--client", "Hooli",
                   "--title", "Intranet", "--amount", "1000"])
    return r


def test_cli_sign_request_and_status(home):
    r = _cli_seed(home)
    res = r.invoke(cli, ["project", "sign-request", "1"])
    assert res.exit_code == 0, res.output
    assert "/sign/" in res.output
    token = res.output.strip().split("/sign/")[1].split()[0]
    assert len(token) > 20

    res = r.invoke(cli, ["project", "sign-status", "1"])
    assert res.exit_code == 0
    assert "awaiting signature" in res.output

    # same link on re-request
    res2 = r.invoke(cli, ["project", "sign-request", "1"])
    assert res2.exit_code == 0
    assert token in res2.output


def test_cli_sign_status_no_request(home):
    r = _cli_seed(home)
    res = r.invoke(cli, ["project", "sign-status", "1"])
    assert res.exit_code == 0
    assert "no signature request yet" in res.output


def test_cli_sign_request_unknown_project(home):
    r = CliRunner()
    res = r.invoke(cli, ["project", "sign-request", "999"])
    assert res.exit_code != 0
    assert "no project" in res.output
