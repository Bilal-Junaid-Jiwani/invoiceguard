"""Agency-mode slice #34: CSV export for the attributed invoice lines
of `freelancer show`.

`freelancer show` got `--csv` in 0.39.0 for the earnings detail, but
the attributed invoice lines underneath it still had to be retyped
from the text view — the per-invoice amount, collected, and
outstanding that the earnings lines only summarize. This slice adds
`--invoices-csv PATH` (or `-` for stdout): one row per attributed
invoice in the same id order the text lines print them, with the
same columns the text lines print (invoice id, client, currency,
amount, collected, outstanding, status) plus the same per-currency
payout timing in trailing `payout_status` / `payout_date` columns,
through one shared library call
`reports.freelancer_show_invoices_csv()`. No new library rules: the
rows are the same `db.list_invoices()` rows the text view prints,
with the same `paid_cents` / `outstanding_cents` from the payment
ledger, and the timing is the same `agency_summary()` rows keyed by
currency, projected with `--as-of`. Drafts and voids stay excluded,
exactly like the text view. The recorded payouts stay text-only.
Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    FREELANCER_SHOW_INVOICES_CSV_COLUMNS,
    agency_summary,
    freelancer_show_invoices_csv,
)


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _detail_freelancer(db, project, name="Ali R.", split_pct=70.0,
                       paid_cents=90_000, payout_cents=25_000,
                       payout_paid_at=None, cadence="weekly", day=3,
                       email=None, phone=None, currency="USD"):
    """Freelancer with one attributed invoice, `paid_cents` collected,
    `payout_cents` already paid out (on `payout_paid_at` when given),
    and a payout schedule."""
    fid = db.add_freelancer(name, email=email, phone=phone,
                            split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid, currency=currency)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, currency,
                         paid_at=payout_paid_at)
    return fid


def _show_invoices_csv_rows(fid, *extra):
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   *extra, "--invoices-csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _attributed(db, fid):
    """The same attributed-invoice dicts `freelancer show` builds."""
    rows = [dict(r) for r in db.list_invoices()
            if r["freelancer_id"] == fid
            and r["status"] not in ("draft", "void")]
    for inv in rows:
        inv["paid_cents"] = db.paid_cents(inv["id"])
        inv["outstanding_cents"] = (inv["amount_cents"]
                                    - inv["paid_cents"])
    return rows


# 2026-10-28 is a Wednesday (ISO weekday 3).


def test_show_invoices_csv_header_and_money_columns(db, sample_project):
    fid = _detail_freelancer(db, sample_project)  # weekly Wed, due today
    rows = _show_invoices_csv_rows(fid, "--as-of", "2026-10-28")
    assert rows[0] == FREELANCER_SHOW_INVOICES_CSV_COLUMNS
    assert len(rows) == 2  # header + one invoice row
    inv = rows[1]
    assert inv[0] == "1"  # freelancer id
    assert inv[1] == "Ali R."
    assert inv[2] == "1"  # invoice id
    assert inv[3] == "Acme Corp"
    assert inv[4] == "USD"
    assert inv[5] == "1000.00"  # amount
    assert inv[6] == "900.00"  # collected
    assert inv[7] == "100.00"  # outstanding
    # The payment flips the stored status from "sent" to
    # "partially-paid" — the export prints the stored status, like
    # the text lines do.
    assert inv[8] == "partially-paid"
    assert inv[9] == "due"
    assert inv[10] == "2026-10-28"  # next payout date


def test_show_invoices_csv_multiple_rows_sorted_drafts_voids_excluded(
        db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    # A second, unpaid invoice; a draft and a void that the text view
    # (and this export) must not carry.
    _invoice(db, sample_project, 50_000, "sent", fid)
    _invoice(db, sample_project, 70_000, "draft", fid)
    _invoice(db, sample_project, 80_000, "void", fid)
    rows = _show_invoices_csv_rows(fid, "--as-of", "2026-10-28")
    assert len(rows) == 3  # header + two real invoices
    assert [r[2] for r in rows[1:]] == ["1", "2"]  # id order
    assert rows[2][5] == "500.00" and rows[2][6] == "0.00"
    assert rows[2][7] == "500.00" and rows[2][8] == "sent"


def test_show_invoices_csv_timing_per_currency(db, sample_project):
    # A USD invoice and a EUR invoice: each row carries its own
    # currency's timing, taken from the same agency summary rows the
    # earnings lines use — not the freelancer's rollup.
    fid = _detail_freelancer(db, sample_project)
    _invoice(db, sample_project, 20_000, "sent", fid, currency="EUR")
    rows = _show_invoices_csv_rows(fid, "--as-of", "2026-10-28")
    timing = {r["currency"]: r for r in
              agency_summary(db, fid, as_of="2026-10-28")}
    for row in rows[1:]:
        t = timing[row[4]]
        assert row[9] == t["payout_status"]
        # Same date rule as the other exports: the missed run date
        # when overdue, the next payout date when due or upcoming,
        # empty for settled / no schedule even when a next date exists.
        if t["payout_status"] == "overdue":
            expected_date = t.get("missed_payout_date") or ""
        elif t["payout_status"] in ("due", "upcoming"):
            expected_date = t.get("next_payout_date") or ""
        else:
            expected_date = ""
        assert row[10] == expected_date


def test_show_invoices_csv_as_of_projection(db, sample_project):
    # Same book, projected to 2026-11-10: weekly Wed's next run is
    # Nov 11, so the invoice row is upcoming, not due.
    fid = _detail_freelancer(db, sample_project)  # weekly Wed
    rows = _show_invoices_csv_rows(fid, "--as-of", "2026-11-10")
    assert rows[1][9] == "upcoming"
    assert rows[1][10] == "2026-11-11"


def test_show_invoices_csv_no_attributed_invoices_header_only(
        db, sample_project):
    fid = db.add_freelancer("No Work Yet")
    rows = _show_invoices_csv_rows(fid)
    assert rows == [FREELANCER_SHOW_INVOICES_CSV_COLUMNS]


def test_show_invoices_csv_file_path_writes_file_and_text_view(
        db, sample_project, tmp_path):
    fid = _detail_freelancer(db, sample_project)
    path = tmp_path / "freelancer-show-invoices.csv"
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-10-28",
                                   "--invoices-csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "freelancer #1: Ali R." in res.output  # text view still prints
    assert "invoices:" in res.output  # invoice lines still print too
    assert (f"wrote freelancer show invoices CSV: {path} (1 rows)"
            in res.output)
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == FREELANCER_SHOW_INVOICES_CSV_COLUMNS
    assert rows[1][1] == "Ali R." and rows[1][9] == "due"


def test_show_invoices_csv_stdout_replaces_text_view(db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-10-28",
                                   "--invoices-csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "freelancer_id,freelancer,invoice_id,client,currency,")
    assert "freelancer #1: Ali R." not in res.output  # no text view


def test_show_invoices_csv_both_csvs_to_files(db, sample_project,
                                              tmp_path):
    # `--csv` and `--invoices-csv` together, each to its own file:
    # both notes print, both files are written, text view still prints.
    fid = _detail_freelancer(db, sample_project)
    earnings = tmp_path / "earnings.csv"
    invoices = tmp_path / "invoices.csv"
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-10-28",
                                   "--csv", str(earnings),
                                   "--invoices-csv", str(invoices)])
    assert res.exit_code == 0, res.output
    assert f"wrote freelancer show CSV: {earnings} (1 rows)" in res.output
    assert (f"wrote freelancer show invoices CSV: {invoices} (1 rows)"
            in res.output)
    assert "freelancer #1: Ali R." in res.output
    assert list(csv.reader(io.StringIO(earnings.read_text())))[1][1] \
        == "Ali R."
    assert list(csv.reader(io.StringIO(invoices.read_text())))[1][1] \
        == "Ali R."


def test_show_invoices_csv_both_csvs_to_stdout_is_clean_error(
        db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--csv", "-",
                                   "--invoices-csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "only one CSV can be printed to stdout" in res.output


def test_show_invoices_csv_invalid_as_of_is_clean_error(
        db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-13-40",
                                   "--invoices-csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_show_invoices_csv_unknown_freelancer_is_clean_error(
        db, sample_project):
    res = CliRunner().invoke(cli, ["freelancer", "show", "99",
                                   "--invoices-csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "no freelancer #99" in res.output


def test_freelancer_show_invoices_csv_bare_library_call(
        db, sample_project):
    # Without a timing map both timing columns stay empty, so a bare
    # call gains no invented status; with the map they carry the same
    # timing the text view prints — and formula-looking names are
    # neutralized on both the freelancer and the client cells.
    fid = _detail_freelancer(db, sample_project,  # weekly Wed, due
                             payout_paid_at="2026-10-20")
    f = db.get_freelancer(fid)
    invoices = _attributed(db, fid)
    bare = list(csv.reader(io.StringIO(
        freelancer_show_invoices_csv(f, invoices))))
    assert bare[0] == FREELANCER_SHOW_INVOICES_CSV_COLUMNS
    assert len(bare) == 2
    assert bare[1][9] == "" and bare[1][10] == ""
    assert bare[1][5] == "1000.00"  # amount needs no timing map
    timing = {r["currency"]: r for r in
              agency_summary(db, fid, as_of="2026-10-31")}
    timed = list(csv.reader(io.StringIO(
        freelancer_show_invoices_csv(f, invoices, timing))))
    assert timed[1][9] == "overdue"
    assert timed[1][10] == "2026-10-28"  # the missed Wed run

    evil_id = db.add_freelancer("=Evil", split_pct=50.0,
                                payout_cadence="monthly", payout_day=15)
    evil_client = db.add_client("=Evil Corp", "evil@example.test")
    evil_project = db.add_project(evil_client, "Bad job", 100_000,
                                  "USD", 50.0, 1.5, 15, "contract")
    iid = db.add_invoice(evil_project, "final", 100_000, "USD",
                         status="sent", due_date="2026-01-01")
    db.assign_invoice_freelancer(iid, evil_id)
    db.record_payment(iid, 80_000, method="bank")
    f_evil = db.get_freelancer(evil_id)
    invoices_evil = _attributed(db, evil_id)
    timing_evil = {r["currency"]: r for r in
                   agency_summary(db, evil_id, as_of="2026-10-31")}
    evil = list(csv.reader(io.StringIO(
        freelancer_show_invoices_csv(f_evil, invoices_evil,
                                     timing_evil))))
    assert evil[1][1] == "'=Evil"  # formula injection neutralized
    assert evil[1][3] == "'=Evil Corp"
    assert evil[1][9] == "overdue"
    assert evil[1][10] == "2026-10-15"
