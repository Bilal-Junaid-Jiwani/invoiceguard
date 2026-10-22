"""Agency-mode slice #3: freelancer earnings (billed vs collected vs outstanding).

The receivables report only showed what each freelancer's clients still
owe. Earnings answer the other half: what the freelancer's attributed
invoices billed, what the payment ledger has actually collected, and
what's still outstanding — per currency, read-only, via
reports.freelancer_earnings and the `freelancer show` CLI command.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import freelancer_earnings


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD", kind="final"):
    iid = db.add_invoice(project["id"], kind, cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def test_earnings_math(db, sample_project):
    fid = db.add_freelancer("Ali R.")
    i1 = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(i1, 40_000, method="bank")
    # invoice #2: full payment recorded
    iid2 = _invoice(db, sample_project, 50_000, "sent", fid)
    db.record_payment(iid2, 50_000, method="stripe")
    # unassigned invoice: belongs to nobody, counted nowhere
    _invoice(db, sample_project, 999_000, "sent")
    # drafts and voids are not bills
    _invoice(db, sample_project, 5_000, "draft", fid)
    _invoice(db, sample_project, 7_000, "void", fid)

    rows = freelancer_earnings(db)
    assert len(rows) == 1
    r = rows[0]
    assert r["freelancer_id"] == fid
    assert r["freelancer_name"] == "Ali R."
    assert r["currency"] == "USD"
    # billed = 100k (sent) + 50k (paid) ; draft/void/unassigned excluded
    assert r["invoiced_cents"] == 150_000
    # collected = 40k partial + 50k full
    assert r["collected_cents"] == 90_000
    assert r["outstanding_cents"] == 60_000
    assert r["invoice_count"] == 2


def test_earnings_per_currency(db, sample_project):
    fid = db.add_freelancer("Sana K.")
    _invoice(db, sample_project, 100_000, "sent", fid, currency="USD")
    _invoice(db, sample_project, 300_000, "paid", fid, currency="EUR")
    rows = freelancer_earnings(db)
    assert len(rows) == 2
    by_cur = {r["currency"]: r for r in rows}
    assert by_cur["USD"]["invoiced_cents"] == 100_000
    assert by_cur["EUR"]["invoiced_cents"] == 300_000
    assert by_cur["EUR"]["collected_cents"] == 0  # paid status, no ledger row


def test_earnings_freelancer_with_no_invoices(db):
    fid = db.add_freelancer("Idle I.")
    rows = freelancer_earnings(db, fid)
    assert len(rows) == 1
    assert rows[0]["freelancer_name"] == "Idle I."
    assert rows[0]["currency"] is None
    assert rows[0]["invoiced_cents"] == 0
    assert rows[0]["invoice_count"] == 0


def test_earnings_unknown_freelancer(db):
    with pytest.raises(ValueError, match="no freelancer #999"):
        freelancer_earnings(db, 999)


def test_freelancer_show_cli(db, sample_project, home):
    """`freelancer show` prints contact, earnings, and invoice lines."""
    fid = db.add_freelancer("Ali R.", "ali@example.test", "+15551234567")
    i1 = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(i1, 40_000, method="bank")
    r = CliRunner().invoke(cli, ["freelancer", "show", str(fid)])
    assert r.exit_code == 0, r.output
    out = r.output
    assert "Ali R." in out
    assert "ali@example.test" in out
    assert "+15551234567" in out
    assert "billed 1,000.00" in out
    assert "collected 400.00" in out
    assert "outstanding 600.00" in out
    assert "Acme Corp" in out


def test_freelancer_show_unknown(db, home):
    r = CliRunner().invoke(cli, ["freelancer", "show", "999"])
    assert r.exit_code != 0
    assert "no freelancer #999" in r.output
