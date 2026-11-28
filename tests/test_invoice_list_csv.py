"""Agency-mode slice #36: CSV export for `invoice list`.

The agency slices #32–#35 put CSV exports on every agency view
(freelancer list/show, payout list/suggest/schedule/due/overdue,
agency summary, invoice report), but the core `invoice list` view
itself was still text-only — the one list an agency operator opens
first to see the whole book. This slice adds `--csv PATH` (or `-`
for stdout): one row per listed invoice in the same id order the
text lines print them, with the same columns the text lines print
(invoice id, kind, status, amount, currency, project, freelancer,
due date), through one shared library call
`reports.invoice_list_csv()`. No new library rules: the rows are
the same `db.list_invoices()` rows the text view prints. Slice #39
(0.45.0) added payout timing to the list: `--as-of DATE` projects
it and the CSV gains trailing `payout_status` / `payout_date`
columns — see `tests/test_invoice_list_timing.py`. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import INVOICE_LIST_CSV_COLUMNS, invoice_list_csv


def _invoice(db, project, cents, kind="final", status="sent",
             freelancer_id=None, currency="USD", due_date="2026-01-01"):
    iid = db.add_invoice(project["id"], kind, cents, currency,
                         status=status, due_date=due_date)
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _list_csv_rows(*extra):
    res = CliRunner().invoke(cli, ["invoice", "list", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def test_invoice_list_csv_header_and_money_columns(db, sample_project):
    _invoice(db, sample_project, 100_000)
    rows = _list_csv_rows()
    assert rows[0] == INVOICE_LIST_CSV_COLUMNS
    assert len(rows) == 2  # header + one invoice row
    inv = rows[1]
    assert inv[0] == "1"  # invoice id
    assert inv[1] == "final"  # kind
    assert inv[2] == "sent"  # status
    assert inv[3] == "1000.00"  # amount
    assert inv[4] == "USD"  # currency
    assert inv[5] == "Website redesign"  # project
    assert inv[6] == ""  # unassigned: no freelancer name
    assert inv[7] == "2026-01-01"  # due date


def test_invoice_list_csv_multiple_rows_in_id_order(db, sample_project):
    _invoice(db, sample_project, 100_000, kind="deposit", status="paid",
             due_date="2026-01-05")
    _invoice(db, sample_project, 50_000, kind="milestone", status="sent",
             due_date="2026-01-10")
    _invoice(db, sample_project, 20_000, kind="final", status="draft",
             due_date=None)
    rows = _list_csv_rows()
    assert len(rows) == 4  # header + three invoice rows
    # Same id order the text lines print them.
    assert [r[0] for r in rows[1:]] == ["1", "2", "3"]
    assert rows[1][1] == "deposit" and rows[1][3] == "1000.00"
    assert rows[2][1] == "milestone" and rows[2][3] == "500.00"
    assert rows[3][1] == "final" and rows[3][3] == "200.00"
    # A draft with no due date carries an empty due-date cell,
    # exactly like the text lines printing `-`.
    assert rows[3][7] == ""


def test_invoice_list_csv_freelancer_filter(db, sample_project):
    fid = db.add_freelancer("Ali R.")
    other = db.add_freelancer("Sara K.")
    _invoice(db, sample_project, 100_000, freelancer_id=fid)
    _invoice(db, sample_project, 50_000, freelancer_id=other)
    _invoice(db, sample_project, 20_000)  # unassigned
    rows = _list_csv_rows("--freelancer", str(fid))
    assert len(rows) == 2  # header + the one attributed invoice
    assert rows[1][0] == "1"
    assert rows[1][6] == "Ali R."
    # The filter composes with the export: the full book still
    # exports all three rows without it.
    assert len(_list_csv_rows()) == 4


def test_invoice_list_csv_no_invoices_header_only(db, sample_project):
    rows = _list_csv_rows()
    assert rows == [INVOICE_LIST_CSV_COLUMNS]


def test_invoice_list_csv_file_path_writes_file_and_text_view(
        db, sample_project, tmp_path):
    fid = db.add_freelancer("Ali R.")
    _invoice(db, sample_project, 100_000, freelancer_id=fid)
    path = tmp_path / "invoice-list.csv"
    res = CliRunner().invoke(cli, ["invoice", "list",
                                   "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "#  1" in res.output  # text view still prints
    assert "[Ali R.]" in res.output  # freelancer bracket still prints
    assert f"wrote invoice list CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == INVOICE_LIST_CSV_COLUMNS
    assert rows[1][6] == "Ali R." and rows[1][3] == "1000.00"


def test_invoice_list_csv_stdout_replaces_text_view(db, sample_project):
    _invoice(db, sample_project, 100_000)
    res = CliRunner().invoke(cli, ["invoice", "list", "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "invoice_id,kind,status,amount,currency,project,")
    assert "#  1" not in res.output  # no text view


def test_invoice_list_csv_unknown_freelancer_is_clean_error(
        db, sample_project):
    _invoice(db, sample_project, 100_000)
    res = CliRunner().invoke(cli, ["invoice", "list", "--freelancer",
                                   "99", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "no freelancer #99" in res.output


def test_invoice_list_csv_bare_library_call(db, sample_project):
    # A bare call needs no timing map and carries empty timing
    # cells — and formula-looking project titles and freelancer
    # names are neutralized on the free-text cells.
    fid = db.add_freelancer("=Evil")
    evil_project = db.get_project(db.add_project(
        1, "=Evil Project", 200_000, "USD", 50.0, 1.5, 15, ""))
    _invoice(db, evil_project, 100_000, freelancer_id=fid)
    rows = db.list_invoices()
    bare = list(csv.reader(io.StringIO(invoice_list_csv(rows))))
    assert bare[0] == INVOICE_LIST_CSV_COLUMNS
    assert len(bare) == 2
    assert bare[1][0] == "1"
    assert bare[1][3] == "1000.00"
    assert bare[1][5] == "'=Evil Project"  # formula injection neutralized
    assert bare[1][6] == "'=Evil"
