"""Agency-mode slice #33: CSV export for `freelancer show`.

`freelancer show` was the last agency detail view with no spreadsheet
export: the invoice report got `--csv` in 0.31.0 and `--payments-csv`
in 0.32.0, the whole-book view in 0.33.0, the payout ledger in 0.34.0,
the pay-run lists in 0.35.0, the suggestions in 0.36.0, the payout
schedules in 0.37.0, and the roster in 0.38.0 — but drilling into one
freelancer still had to be retyped from the text view. This slice adds
`--csv PATH` (or `-` for stdout) to `freelancer show`: one row per
currency earnings line with the same contact, schedule, and money
columns the text lines print — including the split-derived expected
share and still-due-to-freelancer columns, which no earlier export
carried — plus the same timing in trailing `payout_status` /
`payout_date` columns, through one shared library call
`reports.freelancer_show_csv()`. No new library rules: the rows are
the same `freelancer_earnings()` rows the text view prints, and the
timing is the same `agency_summary()` rows keyed by currency,
projected with `--as-of`. The attributed invoice lines and the
recorded payouts stay text-only. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    FREELANCER_SHOW_CSV_COLUMNS,
    agency_summary,
    freelancer_earnings,
    freelancer_show_csv,
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


def _show_csv_rows(fid, *extra):
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   *extra, "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


# 2026-10-28 is a Wednesday (ISO weekday 3).


def test_show_csv_header_and_money_columns(db, sample_project):
    fid = _detail_freelancer(db, sample_project)  # weekly Wed, due today
    rows = _show_csv_rows(fid, "--as-of", "2026-10-28")
    assert rows[0] == FREELANCER_SHOW_CSV_COLUMNS
    assert len(rows) == 2  # header + one currency row
    ali = rows[1]
    assert ali[0] == "1"  # freelancer id
    assert ali[1] == "Ali R."
    assert ali[2] == ""  # no email set
    assert ali[3] == ""  # no phone set
    assert ali[4] == "70.0"  # stored split percentage
    assert ali[5] == "weekly"
    assert ali[6] == "3"
    assert ali[7] == "USD"
    assert ali[8] == "1000.00"  # billed
    assert ali[9] == "900.00"  # collected
    assert ali[10] == "100.00"  # outstanding
    assert ali[11] == "1"  # invoice count
    assert ali[12] == "250.00"  # paid out
    assert ali[13] == "650.00"  # still owed
    assert ali[14] == "630.00"  # expected share: 70% of collected
    assert ali[15] == "380.00"  # still due: share minus paid out
    assert ali[16] == "due"
    assert ali[17] == "2026-10-28"  # next payout date


def test_show_csv_contact_and_schedule_columns(db, sample_project):
    # Email and phone export exactly as stored, in the same columns
    # the text view prints them; the schedule exports as cadence +
    # day, empty when no schedule is set.
    fid = _detail_freelancer(db, sample_project,
                             email="ali@example.test",
                             phone="+15551234567",
                             payout_cents=0,  # nothing paid since the
                             # missed run, so it is still overdue
                             cadence="monthly", day=15)
    rows = _show_csv_rows(fid, "--as-of", "2026-10-31")
    ali = rows[1]
    assert ali[1] == "Ali R."
    assert ali[2] == "ali@example.test"
    # A leading "+" is a CSV formula lead character, so the phone is
    # neutralized with a leading quote (see `_csv_cell`) — the stored
    # value is unchanged, only its spreadsheet interpretation.
    assert ali[3] == "'+15551234567"
    assert ali[5] == "monthly"
    assert ali[6] == "15"
    assert ali[16] == "overdue"
    assert ali[17] == "2026-10-15"  # the missed run date


def test_show_csv_no_split_row(db, sample_project):
    # No split set: the split cell and both share cells are empty,
    # exactly like the text view omitting the split and the share,
    # rather than an invented 0.
    fid = db.add_freelancer("Nadia B.", email="nadia@example.test")
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    rows = _show_csv_rows(fid, "--as-of", "2026-10-28")
    nadia = rows[1]
    assert nadia[1] == "Nadia B."
    assert nadia[2] == "nadia@example.test"
    assert nadia[4] == ""  # no split set
    assert nadia[5] == ""  # no schedule set
    assert nadia[6] == ""
    assert nadia[9] == "800.00"
    assert nadia[14] == ""  # no expected share without a split
    assert nadia[15] == ""  # no still-due without a split
    assert nadia[16] == "no schedule"
    assert nadia[17] == ""


def test_show_csv_upcoming_timing(db, sample_project):
    # Monthly day 15, as of 2026-11-10: the Oct 15 run was missed, but
    # a payout dated after it (Oct 20) means the run is not still owed
    # — the share still due waits for the next run, Nov 15.
    fid = db.add_freelancer("Omar T.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    rows = _show_csv_rows(fid, "--as-of", "2026-11-10")
    omar = rows[1]
    assert omar[1] == "Omar T."
    assert omar[16] == "upcoming"
    assert omar[17] == "2026-11-15"


def test_show_csv_settled_timing(db, sample_project):
    # The whole still-due share already paid out: settled, with an
    # empty date rather than a next run that needs no payment.
    fid = _detail_freelancer(db, sample_project, payout_cents=63_000)
    rows = _show_csv_rows(fid, "--as-of", "2026-10-28")
    ali = rows[1]
    assert ali[14] == "630.00"
    assert ali[15] == "0.00"  # share fully paid out
    assert ali[16] == "settled"
    assert ali[17] == ""


def test_show_csv_no_billable_row(db, sample_project):
    # A scheduled freelancer with no billable invoices gets one row
    # with an empty currency, zero amounts, empty share cells, and
    # empty timing cells rather than a misleading status, exactly
    # like the text view's no-billable line.
    fid = db.add_freelancer("Noor F.", split_pct=70.0,
                            payout_cadence="weekly", payout_day=3)
    rows = _show_csv_rows(fid, "--as-of", "2026-10-28")
    noor = rows[1]
    assert noor[1] == "Noor F."
    assert noor[4] == "70.0"
    assert noor[7] == ""
    assert noor[8] == "0.00"
    assert noor[9] == "0.00"
    assert noor[11] == "0"
    assert noor[14] == ""  # no share on the no-billable row
    assert noor[15] == ""
    assert noor[16] == ""
    assert noor[17] == ""


def test_show_csv_multi_currency_rows(db, sample_project):
    # One row per currency, in the same order the text view prints
    # them (largest collected first), each with its own timing.
    fid = db.add_freelancer("Ali R.", split_pct=70.0,
                            payout_cadence="weekly", payout_day=3)
    usd = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(usd, 90_000, method="bank")
    db.record_payout(fid, 25_000, "USD")
    eur = _invoice(db, sample_project, 50_000, "sent", fid,
                   currency="EUR")
    db.record_payment(eur, 40_000, method="bank")
    rows = _show_csv_rows(fid, "--as-of", "2026-10-28")
    assert len(rows) == 3  # header + USD + EUR
    assert rows[1][7] == "USD"  # largest collected first
    assert rows[1][9] == "900.00"
    assert rows[1][16] == "due"
    assert rows[2][7] == "EUR"
    assert rows[2][9] == "400.00"
    assert rows[2][14] == "280.00"  # 70% of EUR collected
    assert rows[2][16] == "due"


def test_show_csv_as_of_projection(db, sample_project):
    # Same book, projected to 2026-11-10: weekly Wed's next run is
    # Nov 11, so the detail is upcoming, not due.
    fid = _detail_freelancer(db, sample_project)  # weekly Wed
    rows = _show_csv_rows(fid, "--as-of", "2026-11-10")
    assert rows[1][16] == "upcoming"
    assert rows[1][17] == "2026-11-11"


def test_show_csv_file_path_writes_file_and_text_view(
        db, sample_project, tmp_path):
    fid = _detail_freelancer(db, sample_project)
    path = tmp_path / "freelancer-show.csv"
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-10-28",
                                   "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "freelancer #1: Ali R." in res.output  # text view still prints
    assert "invoices:" in res.output  # invoice lines stay text-only
    assert f"wrote freelancer show CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == FREELANCER_SHOW_CSV_COLUMNS
    assert rows[1][1] == "Ali R." and rows[1][16] == "due"


def test_show_csv_stdout_replaces_text_view(db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-10-28",
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "freelancer_id,freelancer,email,phone,split_pct,")
    assert "freelancer #1: Ali R." not in res.output  # no text view


def test_show_csv_invalid_as_of_is_clean_error(db, sample_project):
    fid = _detail_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   "--as-of", "2026-13-40",
                                   "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_show_csv_unknown_freelancer_is_clean_error(db, sample_project):
    res = CliRunner().invoke(cli, ["freelancer", "show", "99",
                                   "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "no freelancer #99" in res.output


def test_freelancer_show_csv_bare_library_call(db, sample_project):
    # Without a timing map both timing columns stay empty, so a bare
    # call gains no invented status; with the map they carry the same
    # timing the text view prints — and a formula-looking freelancer
    # name is neutralized.
    fid = _detail_freelancer(db, sample_project,  # weekly Wed, due
                             payout_paid_at="2026-10-20")
    f = db.get_freelancer(fid)
    rows = freelancer_earnings(db, fid)
    bare = list(csv.reader(io.StringIO(freelancer_show_csv(f, rows))))
    assert bare[0] == FREELANCER_SHOW_CSV_COLUMNS
    assert len(bare) == 2
    assert all(r[16] == "" and r[17] == "" for r in bare[1:])
    assert bare[1][14] == "630.00"  # share needs no timing map
    timing = {r["currency"]: r for r in
              agency_summary(db, fid, as_of="2026-10-31")}
    timed = list(csv.reader(io.StringIO(
        freelancer_show_csv(f, rows, timing))))
    assert timed[1][16] == "overdue"
    assert timed[1][17] == "2026-10-28"  # the missed Wed run

    evil_id = db.add_freelancer("=Evil", split_pct=50.0,
                                payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", evil_id)
    db.record_payment(iid, 80_000, method="bank")
    f_evil = db.get_freelancer(evil_id)
    rows_evil = freelancer_earnings(db, evil_id)
    timing_evil = {r["currency"]: r for r in
                   agency_summary(db, evil_id, as_of="2026-10-31")}
    evil = list(csv.reader(io.StringIO(
        freelancer_show_csv(f_evil, rows_evil, timing_evil))))
    assert evil[1][1] == "'=Evil"  # formula injection neutralized
    assert evil[1][16] == "overdue"
    assert evil[1][17] == "2026-10-15"
