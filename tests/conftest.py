"""Shared fixtures: isolated config dir + SQLite DB per test."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from invoiceguard.contracts import render_contract
from invoiceguard.db import DB


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated ~/.invoiceguard: config + db + templates."""
    cfg_dir = tmp_path / "invoiceguard_home"
    cfg_dir.mkdir()
    monkeypatch.setenv("INVOICEGUARD_CONFIG", str(cfg_dir / "config.yaml"))
    monkeypatch.setenv("INVOICEGUARD_DB", str(cfg_dir / "invoiceguard.db"))
    # templates live next to the config file
    import invoiceguard

    src_templates = Path(invoiceguard.__file__).resolve().parent / "email_templates"
    dest = cfg_dir / "templates"
    shutil.copytree(src_templates, dest)
    return cfg_dir


@pytest.fixture()
def db(home):
    d = DB(os.environ["INVOICEGUARD_DB"])
    yield d
    d.close()


@pytest.fixture()
def sample_client(db):
    cid = db.add_client("Acme Corp", "billing@acme.test")
    return db.get_client(cid)


@pytest.fixture()
def sample_project(db, sample_client):
    contract = render_contract(
        sample_client["name"], sample_client["email"],
        "Website redesign", 200_000, "USD", 50.0, 1.5, 15,
    )
    pid = db.add_project(
        sample_client["id"], "Website redesign", 200_000, "USD",
        50.0, 1.5, 15, contract,
    )
    return db.get_project(pid)


@pytest.fixture()
def smtp_cfg():
    return {
        "host": "127.0.0.1",
        "port": 1025,
        "username": "",
        "password": "",
        "from_addr": "freelancer@example.test",
        "use_tls": False,
    }
