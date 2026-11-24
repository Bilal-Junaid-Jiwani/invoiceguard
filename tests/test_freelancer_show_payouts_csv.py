"""Agency-mode slice #35: CSV export for the recorded payouts of
`freelancer show`.

`freelancer show` got `--csv` in 0.39.0 for the earnings detail and
`--invoices-csv` in 0.40.0 for the attributed invoice lines, but the
recorded payouts underneath them still had to be retyped from the
text view — the per-payout amount, date, method, and note that the
earnings lines only summarize as paid-out totals. This slice adds
`--payouts-csv PATH` (or `-` for stdout): one row per recorded
payout in the same newest-first order the text lines print them,
with the same columns the text lines print (payout id, currency,
amount, paid date, method, note) plus the same per-currency payout
timing in trailing `payout_status` / `payout_date` columns, through
one shared library call `reports.freelancer_show_payouts_csv()`.
No new library rules: the rows are the same `db.list_payouts()`
rows the text view prints, and the timing is the same
`agency_summary()` rows keyed by currency, projected with
`--as-of`. With all three exports the whole `freelancer show`
screen is now a spreadsheet. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    FREELANCER_SHOW_PAYOUTS_CSV_COLUMNS,
    agency_summary,
    freelancer_show_payouts_csv,
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
                       payout_paid_at="2026-10-20", cadence="weekly",
                       day=3, email=None, phone=None, currency="USD",
                       method="bank", note=None):
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
                         paid_at=payout_paid_at, method=method,
                         note=note)
    return fid


def _show_payouts_csv_rows(fid, *extra):
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   *extra, "--payouts-csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


# 2026-10-28 is a Wednesday (ISO weekday 3).


def test_show_payouts_csv_header_and_money_columns(db, sample_project):
    fid = _detail_freelancer(db, sample_project)  # weekly Wed, due today
    rows = _show_payouts_csv_rows(fid, "--as-of", "2026-10-28")
    assert rows[0] == FREELANCER_SHOW_PAYOUTS_CSV_COLUMNS
    assert len(rows) == 2  # header + one payout row
    pay = rows[1]
    assert pay[0] == "1"  # freelancer id
    assert pay[1] == "Ali R."
    assert pay[2] == "1"  # payout id
    assert pay[3] == "USD"
    assert pay[4] == "250.00"  # amount
    # The full stored paid_at, like the `payout list` CSV export —
    # the text view truncates it to the date for display.
    assert pay[5] == "2026-10-20T00:00:00"
    assert pay[6] == "bank"  # method
    assert pay[7] == ""  # no note
    assert pay[8] == "due"
    assert pay[9] == "2026-10-28"  # next payout date


def test_show_payouts_csv_multiple_rows_newest_first(db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    # A second, older payout with a note; the over-payout guard
    # caps the ledger at the 900.00 collected, so 100.00 fits.
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-13",
                     method="cash", note="first tranche")
    rows = _show_payouts_csv_rows(fid, "--as-of", "2026-10-28")
    assert len(rows) == 3  # header + two payout rows
    # Newest first, like the text lines: the 2026-10-20 payout
    # before the 2026-10-13 one.
    assert [r[2] for r in rows[1:]] == ["1", "2"]
    assert rows[1][4] == "250.00" and rows[1][6] == "bank"
    assert rows[2][4] == "100.00" and rows[2][6] == "cash"
    assert rows[2][7] == "first tranche"


def test_show_payouts_csv_timing_per_currency(db, sample_project):
    # A USD payout and a EUR payout: each row carries its own
    # currency's timing, taken from the same agency summary rows the
    # earnings lines use — not the freelancer's rollup.
    fid = _detail_freelancer(db, sample_project)
    _invoice(db, sample_project, 20_000, "sent", fid, currency="EUR")
    db.record_payment(2, 20_000, method="bank")
    db.record_payout(fid, 5_000, "EUR", paid_at="2026-10-21")
    rows = _show_payouts_csv_rows(fid, "--as-of", "2026-10-28")
    timing = {r["currency"]: r for r in
              agency_summary(db, fid, as_of="2026-10-28")}
    for row in rows[1:]:
        t = timing[row[3]]
        assert row[8] == t["payout_status"]
        # Same date rule as the other exports: the missed run date
        # when overdue, the next payout date when due or upcoming,
        # empty for settled / no schedule even when a next date exists.
        if t["payout_status"] == "overdue":
            expected_date = t.get("missed_payout_date") or ""
        elif t["payout_status"] in ("due", "upcoming"):
            expected_date = t.get("next_payout_date") or ""
        else:
            expected_date = ""
        assert row[9] == expected_date


def test_show_payouts_csv_as_of_projection(db, sample_project):
    # Same book, projected to 2026-10-31: weekly Wed's last run was
    # missed on Oct 28, so the payout row is overdue, not due.
    fid = _detail_freelancer(db, sample_project)  # weekly Wed
    rows = _show_payouts_csv_rows(fid, "--as-of", "2026-10-31")
    assert rows[1][8] == "overdue"
    assert rows[1][9] == "2026-10-28"  # the missed Wed run


def test_show_payouts_csv_no_payouts_header_only(db, sample_project):
    fid = _detail_freelancer(db, sample_project, payout_cents=0)
    rows = _show_payouts_csv_rows(fid)
    assert rows == [FREELANCER_SHOW_PAYOUTS_CSV_COLUMNS]


def test_show_payouts_csv_file_path_writes_file_and_text_view(
        db, sample_project, tmp_path):
    fid = _detail_freelancer(db, sample_project)
    path = tmp_path / "freelancer-show-payouts.csv"
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-10-28",
                                   "--payouts-csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "freelancer #1: Ali R." in res.output  # text view still prints
    assert "payouts:" in res.output  # payout lines still print too
    assert (f"wrote freelancer show payouts CSV: {path} (1 rows)"
            in res.output)
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == FREELANCER_SHOW_PAYOUTS_CSV_COLUMNS
    assert rows[1][1] == "Ali R." and rows[1][8] == "due"


def test_show_payouts_csv_stdout_replaces_text_view(db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-10-28",
                                   "--payouts-csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "freelancer_id,freelancer,payout_id,currency,amount,")
    assert "freelancer #1: Ali R." not in res.output  # no text view


def test_show_payouts_csv_all_three_csvs_to_files(db, sample_project,
                                                  tmp_path):
    # `--csv`, `--invoices-csv`, and `--payouts-csv` together, each
    # to its own file: all three notes print, all three files are
    # written, text view still prints.
    fid = _detail_freelancer(db, sample_project)
    earnings = tmp_path / "earnings.csv"
    invoices = tmp_path / "invoices.csv"
    payouts = tmp_path / "payouts.csv"
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-10-28",
                                   "--csv", str(earnings),
                                   "--invoices-csv", str(invoices),
                                   "--payouts-csv", str(payouts)])
    assert res.exit_code == 0, res.output
    assert f"wrote freelancer show CSV: {earnings} (1 rows)" in res.output
    assert (f"wrote freelancer show invoices CSV: {invoices} (1 rows)"
            in res.output)
    assert (f"wrote freelancer show payouts CSV: {payouts} (1 rows)"
            in res.output)
    assert "freelancer #1: Ali R." in res.output
    assert list(csv.reader(io.StringIO(earnings.read_text())))[1][1] \
        == "Ali R."
    assert list(csv.reader(io.StringIO(invoices.read_text())))[1][1] \
        == "Ali R."
    assert list(csv.reader(io.StringIO(payouts.read_text())))[1][1] \
        == "Ali R."


def test_show_payouts_csv_two_csvs_to_stdout_is_clean_error(
        db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--invoices-csv", "-",
                                   "--payouts-csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "only one CSV can be printed to stdout" in res.output


def test_show_payouts_csv_invalid_as_of_is_clean_error(
        db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-13-40",
                                   "--payouts-csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_show_payouts_csv_unknown_freelancer_is_clean_error(
        db, sample_project):
    res = CliRunner().invoke(cli, ["freelancer", "show", "99",
                                   "--payouts-csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "no freelancer #99" in res.output


def test_freelancer_show_payouts_csv_bare_library_call(
        db, sample_project):
    # Without a timing map both timing columns stay empty, so a bare
    # call gains no invented status; with the map they carry the same
    # timing the text view prints — and formula-looking names are
    # neutralized on the freelancer, method, and note cells.
    fid = _detail_freelancer(db, sample_project)  # weekly Wed, due
    f = db.get_freelancer(fid)
    payouts = db.list_payouts(fid)
    bare = list(csv.reader(io.StringIO(
        freelancer_show_payouts_csv(f, payouts))))
    assert bare[0] == FREELANCER_SHOW_PAYOUTS_CSV_COLUMNS
    assert len(bare) == 2
    assert bare[1][8] == "" and bare[1][9] == ""
    assert bare[1][4] == "250.00"  # amount needs no timing map
    timing = {r["currency"]: r for r in
              agency_summary(db, fid, as_of="2026-10-31")}
    timed = list(csv.reader(io.StringIO(
        freelancer_show_payouts_csv(f, payouts, timing))))
    assert timed[1][8] == "overdue"
    assert timed[1][9] == "2026-10-28"  # the missed Wed run

    evil_id = db.add_freelancer("=Evil", split_pct=50.0,
                                payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", evil_id)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(evil_id, 10_000, "USD", paid_at="2026-10-10",
                     method="=Evil method", note="=Evil note")
    f_evil = db.get_freelancer(evil_id)
    payouts_evil = db.list_payouts(evil_id)
    timing_evil = {r["currency"]: r for r in
                   agency_summary(db, evil_id, as_of="2026-10-31")}
    evil = list(csv.reader(io.StringIO(
        freelancer_show_payouts_csv(f_evil, payouts_evil,
                                    timing_evil))))
    assert evil[1][1] == "'=Evil"  # formula injection neutralized
    assert evil[1][6] == "'=Evil method"
    assert evil[1][7] == "'=Evil note"
    assert evil[1][8] == "overdue"
    assert evil[1][9] == "2026-10-15"
