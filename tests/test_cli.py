"""CLI smoke tests."""

import types

import stripe
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.db import DB


def test_client_add_and_list(home):
    r = CliRunner()
    assert r.invoke(cli, ["client", "add", "--name", "Initech",
                          "--email", "x@y.test"]).exit_code == 0
    out = r.invoke(cli, ["client", "list"]).output
    assert "Initech" in out and "x@y.test" in out


def test_project_create_requires_existing_client(home):
    r = CliRunner()
    res = r.invoke(cli, ["project", "create", "--client", "Nobody",
                         "--title", "T", "--amount", "100"])
    assert res.exit_code != 0
    assert "no client" in res.output


def _seed(home, monkeypatch):
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    r.invoke(cli, ["project", "create", "--client", "Hooli",
                   "--title", "Intranet", "--amount", "1000"])
    monkeypatch.setattr(
        stripe.PaymentLink, "create",
        lambda **kw: types.SimpleNamespace(
            url="https://buy.stripe.com/x", id="plink_x"))
    monkeypatch.setenv("INVOICEGUARD_STRIPE_SECRET_KEY", "sk_test_000")
    return r


def test_invoice_create_kinds_and_defaults(home, monkeypatch):
    r = _seed(home, monkeypatch)
    db = DB(home / "invoiceguard.db")
    for kind, expected_cents in (("deposit", 50_000),
                                 ("final", 50_000),
                                 ("milestone", 100_000)):
        res = r.invoke(cli, ["invoice", "create", "--project", "1",
                             "--kind", kind])
        assert res.exit_code == 0, res.output
    rows = db.list_invoices()
    assert [x["amount_cents"] for x in rows] == [50_000, 50_000, 100_000]
    db.close()


def test_invoice_create_amount_override(home, monkeypatch):
    r = _seed(home, monkeypatch)
    res = r.invoke(cli, ["invoice", "create", "--project", "1",
                         "--kind", "milestone", "--amount", "250"])
    assert res.exit_code == 0, res.output
    db = DB(home / "invoiceguard.db")
    assert db.list_invoices()[0]["amount_cents"] == 25_000
    db.close()


def test_invoice_list_mark_paid_void(home, monkeypatch):
    r = _seed(home, monkeypatch)
    r.invoke(cli, ["invoice", "create", "--project", "1", "--kind", "deposit"])
    assert "sent" in r.invoke(cli, ["invoice", "list"]).output
    assert r.invoke(cli, ["invoice", "mark-paid", "1"]).exit_code == 0
    assert "paid" in r.invoke(cli, ["invoice", "list"]).output
    assert r.invoke(cli, ["invoice", "void", "1"]).exit_code == 0
    assert "void" in r.invoke(cli, ["invoice", "list"]).output


def test_project_ack(home):
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    r.invoke(cli, ["project", "create", "--client", "Hooli",
                   "--title", "T", "--amount", "500"])
    assert "no-ack" in r.invoke(cli, ["project", "list"]).output
    assert r.invoke(cli, ["project", "ack", "1"]).exit_code == 0
    assert "ack" in r.invoke(cli, ["project", "list"]).output


def test_check_due_refuses_without_smtp(home):
    r = CliRunner()
    res = r.invoke(cli, ["check-due"])
    assert res.exit_code != 0
    assert "SMTP is not configured" in res.output


def test_project_create_amount_rounds_half_up(home):
    # Money convention (late_fees.py) is half-up: 1.005 must be 101 cents,
    # not the 100 that float + banker's rounding produced.
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    res = r.invoke(cli, ["project", "create", "--client", "Hooli",
                         "--title", "T", "--amount", "1.005"])
    assert res.exit_code == 0, res.output
    db = DB(home / "invoiceguard.db")
    assert db.get_project(1)["amount_cents"] == 101
    db.close()


def test_amounts_reject_garbage_and_nonpositive(home):
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    for bad in ("abc", "0", "-50", "1,000"):
        res = r.invoke(cli, ["project", "create", "--client", "Hooli",
                             "--title", "T", "--amount", bad])
        assert res.exit_code != 0, (bad, res.output)
    db = DB(home / "invoiceguard.db")
    assert db.list_projects() == []  # nothing was written
    db.close()


def test_record_payment_rounds_half_up(home, monkeypatch):
    r = _seed(home, monkeypatch)
    r.invoke(cli, ["invoice", "create", "--project", "1", "--kind", "deposit"])
    res = r.invoke(cli, ["invoice", "record-payment", "1",
                         "--amount", "10.075"])
    assert res.exit_code == 0, res.output
    db = DB(home / "invoiceguard.db")
    assert db.payments(1)[0]["amount_cents"] == 1008
    db.close()


def test_deposit_split_rounds_half_up(home, monkeypatch):
    # 5 cents at 50%: half-up deposit is 3 cents (banker's gave 2), and
    # deposit + final still sum to the project total.
    r = CliRunner()
    r.invoke(cli, ["client", "add", "--name", "Hooli"])
    r.invoke(cli, ["project", "create", "--client", "Hooli",
                   "--title", "Tiny", "--amount", "0.05"])
    monkeypatch.setattr(
        stripe.PaymentLink, "create",
        lambda **kw: types.SimpleNamespace(
            url="https://buy.stripe.com/x", id="plink_x"))
    monkeypatch.setenv("INVOICEGUARD_STRIPE_SECRET_KEY", "sk_test_000")
    assert r.invoke(cli, ["invoice", "create", "--project", "1",
                          "--kind", "deposit"]).exit_code == 0
    assert r.invoke(cli, ["invoice", "create", "--project", "1",
                          "--kind", "final"]).exit_code == 0
    db = DB(home / "invoiceguard.db")
    assert [x["amount_cents"] for x in db.list_invoices()] == [3, 2]
    db.close()


def test_dashboard_launches_uvicorn(home, monkeypatch):
    import uvicorn
    from invoiceguard.web.main import app as web_app

    calls = {}

    def fake_run(app, host=None, port=None, **kwargs):
        calls.update(app=app, host=host, port=port)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    out = CliRunner().invoke(cli, ["dashboard"]).output
    assert "http://127.0.0.1:8000/" in out
    assert calls["app"] is web_app
    assert calls["host"] == "127.0.0.1" and calls["port"] == 8000

    out = CliRunner().invoke(cli, ["dashboard", "--port", "9000"]).output
    assert "http://127.0.0.1:9000/" in out
    assert calls["port"] == 9000
