"""Agency-mode slice #38: CSV export for `invoice late-fees`.

The agency slices #32–#37 put CSV exports on every agency view and
every CLI list view — but the late-fee accrual view itself was still
text-only: the one invoice's due date, rate, grace window, months
billed, accrued fees, and total due still had to be retyped from the
text lines. This slice adds `--csv PATH` (or `-` for stdout): one row
with the same columns the text view prints, through one shared
library call `reports.late_fees_csv()`. No new library rules: the row
is the same `db.invoice_full()` dict and `late_fee_summary()` dict
the text view prints, projected with `--as-of` (default: today).
Read-only.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.late_fees import late_fee_summary
from invoiceguard.reports import LATE_FEES_CSV_COLUMNS, late_fees_csv


def _invoice(db, project, cents, kind="final", status="sent",
             due_date="2026-01-01"):
    return db.add_invoice(project["id"], kind, cents, "USD",
                          status=status, due_date=due_date)


def _late_fees_csv_rows(*extra):
    res = CliRunner().invoke(cli, ["invoice", "late-fees", "1",
                                   *extra, "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def test_late_fees_csv_header_and_money_columns(db, sample_project):
    # due 2026-01-01, grace 15 -> accrual starts 2026-01-17; as of
    # 2026-03-17 two full months have elapsed: 1.5% of 100000 = 1500,
    # then 1.5% of 101500 = 1522.5 -> 1523 half-up. Fees 3023.
    _invoice(db, sample_project, 100_000)
    rows = _late_fees_csv_rows("--as-of", "2026-03-17")
    assert rows[0] == LATE_FEES_CSV_COLUMNS
    assert len(rows) == 2  # header + the one accrual row
    row = rows[1]
    assert row[0] == "1"  # invoice id
    assert row[1] == "Acme Corp"  # client
    assert row[2] == "Website redesign"  # project
    assert row[3] == "USD"  # currency
    assert row[4] == "2026-01-01"  # due date
    assert row[5] == "1.5"  # monthly rate
    assert row[6] == "15"  # grace days
    assert row[7] == "2026-01-17"  # accrual start
    assert row[8] == "2026-03-17"  # as-of date
    assert row[9] == "1000.00"  # outstanding
    assert row[10] == "2"  # months billed
    assert row[11] == "30.23"  # accrued fees
    assert row[12] == "1030.23"  # total due


def test_late_fees_csv_accrual_not_started(db, sample_project):
    # As of 2026-06-05 the accrual start (2026-06-17) is still ahead:
    # no months, no fees, and an empty accrual-start cell, exactly
    # like the text view omitting the "fees accrue from" phrase.
    _invoice(db, sample_project, 100_000, due_date="2026-06-01")
    rows = _late_fees_csv_rows("--as-of", "2026-06-05")
    row = rows[1]
    assert row[4] == "2026-06-01"
    assert row[7] == ""
    assert row[8] == "2026-06-05"
    assert row[9] == "1000.00"
    assert row[10] == "0"
    assert row[11] == "0.00"
    assert row[12] == "1000.00"


def test_late_fees_csv_accrual_started_no_full_month(db, sample_project):
    # Accrual started 2026-01-17 but no full month has elapsed by
    # 2026-01-20: zero fees, but the accrual start is still reported,
    # exactly like the text view.
    _invoice(db, sample_project, 100_000)
    rows = _late_fees_csv_rows("--as-of", "2026-01-20")
    row = rows[1]
    assert row[7] == "2026-01-17"
    assert row[10] == "0"
    assert row[11] == "0.00"
    assert row[12] == "1000.00"


def test_late_fees_csv_no_due_date(db, sample_project):
    # No due date: no fees can accrue — empty due-date and
    # accrual-start cells, zero months/fees, total is the outstanding.
    _invoice(db, sample_project, 100_000, due_date=None)
    rows = _late_fees_csv_rows("--as-of", "2026-03-17")
    row = rows[1]
    assert row[4] == ""
    assert row[7] == ""
    assert row[10] == "0"
    assert row[11] == "0.00"
    assert row[12] == "1000.00"


def test_late_fees_csv_partial_payment_accrues_on_outstanding(
        db, sample_project):
    # A 40000 payment leaves 60000 outstanding: month 1 is 1.5% of
    # 60000 = 900, month 2 is 1.5% of 60900 = 913.5 -> 914 half-up.
    iid = _invoice(db, sample_project, 100_000)
    db.record_payment(iid, 40_000)
    rows = _late_fees_csv_rows("--as-of", "2026-03-17")
    row = rows[1]
    assert row[9] == "600.00"
    assert row[10] == "2"
    assert row[11] == "18.14"
    assert row[12] == "618.14"


def test_late_fees_csv_default_as_of_is_today(db, sample_project):
    _invoice(db, sample_project, 100_000)
    rows = _late_fees_csv_rows()
    assert rows[1][8] == date.today().isoformat()


def test_late_fees_csv_file_path_writes_file_and_text_view(
        db, sample_project, tmp_path):
    _invoice(db, sample_project, 100_000)
    path = tmp_path / "late-fees.csv"
    res = CliRunner().invoke(cli, ["invoice", "late-fees", "1",
                                   "--as-of", "2026-03-17",
                                   "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "late-fee accrual" in res.output  # text view still prints
    assert "accrued fees:" in res.output
    assert f"wrote invoice late-fees CSV: {path} (1 row)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == LATE_FEES_CSV_COLUMNS
    assert rows[1][11] == "30.23"


def test_late_fees_csv_stdout_replaces_text_view(db, sample_project):
    _invoice(db, sample_project, 100_000)
    res = CliRunner().invoke(cli, ["invoice", "late-fees", "1",
                                   "--as-of", "2026-03-17",
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "invoice_id,client,project,currency,due_date,")
    assert "late-fee accrual" not in res.output  # no text view


def test_late_fees_csv_unknown_invoice_is_clean_error(db, sample_project):
    res = CliRunner().invoke(cli, ["invoice", "late-fees", "99",
                                   "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "no invoice #99" in res.output


def test_late_fees_csv_bad_as_of_is_clean_error(db, sample_project):
    _invoice(db, sample_project, 100_000)
    res = CliRunner().invoke(cli, ["invoice", "late-fees", "1",
                                   "--as-of", "not-a-date",
                                   "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "bad --as-of date" in res.output


def test_late_fees_csv_bare_library_call():
    # A bare call needs only the invoice dict and its summary, and
    # formula-looking client/project names are neutralized on the
    # free-text cells.
    invoice = {"id": 7, "client_name": "=Evil",
               "project_title": "=Evil Project", "currency": "USD",
               "due_date": "2026-01-01", "late_fee_pct": 1.5,
               "late_fee_grace_days": 15, "outstanding_cents": 100_000}
    summary = late_fee_summary(invoice, as_of="2026-03-17")
    rows = list(csv.reader(io.StringIO(
        late_fees_csv(invoice, summary, as_of="2026-03-17"))))
    assert rows[0] == LATE_FEES_CSV_COLUMNS
    assert len(rows) == 2
    row = rows[1]
    assert row[0] == "7"
    assert row[1] == "'=Evil"  # formula injection neutralized
    assert row[2] == "'=Evil Project"
    assert row[7] == "2026-01-17"
    assert row[10] == "2"
    assert row[11] == "30.23"
    assert row[12] == "1030.23"
