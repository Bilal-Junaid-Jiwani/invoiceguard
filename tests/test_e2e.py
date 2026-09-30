"""End-to-end fixture test:

init -> client -> project -> invoice (mocked Stripe) -> check-due against
a REAL local SMTP server (minimal socket implementation, no TLS/auth) ->
assert emails captured + dunning_events rows written.
"""

from __future__ import annotations

import socketserver
import threading
import types
from datetime import date, timedelta

import pytest
import stripe
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.db import DB
from invoiceguard.dunning import check_due


class _SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 localhost test smtp\r\n")
        data_lines = []
        in_data = False
        while True:
            line = self.rfile.readline().decode("utf-8", "replace")
            if not line:
                break
            upper = line.strip().upper()
            if in_data:
                if line.strip() == ".":
                    in_data = False
                    self.server.messages.append("".join(data_lines))
                    data_lines = []
                    self.wfile.write(b"250 OK\r\n")
                else:
                    data_lines.append(line)
            elif upper.startswith("EHLO") or upper.startswith("HELO"):
                self.wfile.write(b"250-localhost\r\n250 8BITMIME\r\n")
            elif upper.startswith("MAIL FROM") or upper.startswith("RCPT TO"):
                self.wfile.write(b"250 OK\r\n")
            elif upper == "DATA":
                in_data = True
                self.wfile.write(b"354 end with .\r\n")
            elif upper == "QUIT":
                self.wfile.write(b"221 bye\r\n")
                break
            elif upper == "RSET":
                data_lines = []
                self.wfile.write(b"250 OK\r\n")
            else:
                self.wfile.write(b"502 unimplemented\r\n")


class DebugSMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr):
        self.messages: list[str] = []
        super().__init__(addr, _SMTPHandler)


@pytest.fixture()
def smtp_server():
    srv = DebugSMTPServer(("127.0.0.1", 0))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, port
    srv.shutdown()
    srv.server_close()


def test_end_to_end(home, smtp_server, monkeypatch):
    server, port = smtp_server
    runner = CliRunner()

    # 1. init
    res = runner.invoke(cli, ["init"])
    assert res.exit_code == 0, res.output
    assert (home / "config.yaml").exists()
    assert (home / "templates" / "day1.md").exists()

    # 2. client
    res = runner.invoke(cli, ["client", "add", "--name", "Globex",
                              "--email", "ap@globex.test"])
    assert res.exit_code == 0, res.output

    # 3. project (contract generated, ack recorded)
    res = runner.invoke(cli, [
        "project", "create",
        "--client", "Globex", "--title", "Landing page",
        "--amount", "2000", "--deposit-pct", "50",
        "--late-fee-pct", "1.5", "--ack",
    ])
    assert res.exit_code == 0, res.output
    assert "1.5% per month" in res.output

    db = DB(home / "invoiceguard.db")
    proj = db.list_projects()[0]
    assert proj["contract_ack"] == 1
    assert proj["contract_ack_at"] is not None
    assert "late-fee clause" in proj["contract_md"].lower()

    # 4. invoice with mocked Stripe
    def fake_create(**kwargs):
        return types.SimpleNamespace(
            url="https://buy.stripe.com/test_e2e", id="plink_test_e2e")

    monkeypatch.setattr(stripe.PaymentLink, "create", fake_create)
    monkeypatch.setenv("INVOICEGUARD_STRIPE_SECRET_KEY",
                       "sk_test_e2e_000000000000")
    res = runner.invoke(cli, ["invoice", "create", "--project", "1",
                              "--kind", "deposit"])
    assert res.exit_code == 0, res.output
    assert "https://buy.stripe.com/test_e2e" in res.output

    inv = db.list_invoices()[0]
    assert inv["status"] == "sent"
    assert inv["amount_cents"] == 100_000  # 50% of $2000
    assert inv["stripe_url"] == "https://buy.stripe.com/test_e2e"

    # make it overdue (16 days -> day15 threshold reachable after day1/day7)
    past = (date.today() - timedelta(days=16)).isoformat()
    db.update_invoice(inv["id"], due_date=past)

    # 5. check-due against the real local SMTP server
    smtp_cfg = {
        "host": "127.0.0.1", "port": port,
        "username": "", "password": "",
        "from_addr": "me@freelancer.test", "use_tls": False,
    }
    results = check_due(db, smtp_cfg)
    assert len(results) == 1
    assert results[0]["stage"] == "day1"

    # 6. assert email actually arrived at the SMTP server
    assert len(server.messages) == 1
    raw = server.messages[0]
    assert "ap@globex.test" in raw or "To: ap@globex.test" in raw
    assert "Landing page" in raw
    assert "https://buy.stripe.com/test_e2e" in raw

    # 7. assert dunning_events row written, status flipped
    assert db.sent_stages(inv["id"]) == {"day1"}
    assert db.get_invoice(inv["id"])["status"] == "overdue"
    db.close()
