"""Agency-mode slice #8: whole-book agency summary.

`payout suggest` lists only actionable rows and `freelancer show`
covers one freelancer at a time; `reports.agency_summary()` + `agency
summary` give the whole book at a glance — one row per freelancer per
currency (billed / collected / outstanding / paid out / still owed,
plus the suggested payout where a split share is still due), with
per-currency totals across the roster. Read-only.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import agency_summary


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _book(db, project):
    """Two freelancers: Ali (70% split, USD) and Sana (no split, EUR).

    Ali: billed 100_000, collected 90_000, paid out 25_000 ->
    still owed 65_000, suggested 38_000 (70% of 90_000 minus 25_000).
    Sana: billed 50_000, collected 50_000, paid out 0 ->
    still owed 50_000, no suggestion (no split set).
    """
    ali = db.add_freelancer("Ali R.", split_pct=70.0)
    iid = _invoice(db, project, 100_000, "sent", ali)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(ali, 25_000, "USD")
    sana = db.add_freelancer("Sana K.")
    iid = _invoice(db, project, 50_000, "sent", sana, currency="EUR")
    db.record_payment(iid, 50_000, method="bank")
    return ali, sana


def test_summary_rows_cover_whole_book(db, sample_project):
    ali, sana = _book(db, sample_project)
    rows = {(r["freelancer_id"], r["currency"]): r
            for r in agency_summary(db)}
    ali_row = rows[(ali, "USD")]
    assert (ali_row["invoiced_cents"], ali_row["collected_cents"],
            ali_row["outstanding_cents"], ali_row["paid_out_cents"],
            ali_row["still_owed_cents"]) == (100_000, 90_000, 10_000,
                                             25_000, 65_000)
    assert ali_row["suggested_cents"] == 38_000
    sana_row = rows[(sana, "EUR")]
    assert (sana_row["invoiced_cents"], sana_row["collected_cents"],
            sana_row["still_owed_cents"]) == (50_000, 50_000, 50_000)
    assert sana_row["suggested_cents"] is None  # no split: no suggestion


def test_summary_includes_freelancer_with_no_invoices(db):
    fid = db.add_freelancer("No Work Yet")
    rows = agency_summary(db)
    assert len(rows) == 1
    assert rows[0]["freelancer_id"] == fid
    assert rows[0]["currency"] is None
    assert rows[0]["invoiced_cents"] == 0
    assert rows[0]["suggested_cents"] is None


def test_summary_excludes_drafts_voids_and_unattributed(db, sample_project):
    fid = db.add_freelancer("Ali R.", split_pct=70.0)
    _invoice(db, sample_project, 100_000, "sent", fid)
    _invoice(db, sample_project, 999_000, "draft", fid)
    _invoice(db, sample_project, 888_000, "void", fid)
    _invoice(db, sample_project, 777_000, "sent")  # unattributed
    rows = agency_summary(db)
    assert len(rows) == 1
    assert rows[0]["invoiced_cents"] == 100_000


def test_summary_unknown_freelancer_raises(db):
    with pytest.raises(ValueError, match="no freelancer #999"):
        agency_summary(db, 999)


def test_cli_summary_prints_sections_and_totals(db, sample_project):
    _book(db, sample_project)
    out = CliRunner().invoke(cli, ["agency", "summary"])
    assert out.exit_code == 0, out.output
    assert "Ali R." in out.output
    assert "split: 70%" in out.output
    assert "Sana K." in out.output
    assert "no split set" in out.output
    assert "380.00" in out.output  # Ali's suggestion
    assert "TOTALS" in out.output
    assert "USD" in out.output and "EUR" in out.output
    # USD totals: billed 1,000.00, collected 900.00, still owed 650.00
    usd_line = [ln for ln in out.output.splitlines()
                if ln.strip().startswith("USD") and "TOTALS" not in ln]
    assert any("1,000.00" in ln and "900.00" in ln and "650.00" in ln
               for ln in usd_line)


def test_cli_summary_empty_roster(db):
    out = CliRunner().invoke(cli, ["agency", "summary"])
    assert out.exit_code == 0, out.output
    assert "no freelancers on the roster" in out.output


def test_cli_summary_freelancer_filter_and_unknown(db, sample_project):
    ali, _sana = _book(db, sample_project)
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--freelancer", str(ali)])
    assert out.exit_code == 0, out.output
    assert "Ali R." in out.output
    assert "Sana K." not in out.output
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--freelancer", "999"])
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output


def test_cli_summary_is_read_only(db, sample_project):
    _book(db, sample_project)
    before = db.paid_out_cents(1, "USD")
    out = CliRunner().invoke(cli, ["agency", "summary"])
    assert out.exit_code == 0, out.output
    assert db.paid_out_cents(1, "USD") == before
