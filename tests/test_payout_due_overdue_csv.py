"""Agency-mode slice #29: CSV export for `payout due` / `payout overdue`.

The pay-run lists were the last agency views with no spreadsheet
export: the invoice report got `--csv` in 0.31.0 and `--payments-csv`
in 0.32.0, the whole-book view in 0.33.0, the payout ledger in
0.34.0 — but the pay run itself ("who do we pay this week, and who
did we miss?") still had to be retyped from the text lists. This
slice adds `--csv PATH` (or `-` for stdout) to both `payout due` and
`payout overdue`: one row per pay-run row with the same columns the
text lists print, through one shared library call
`reports.pay_run_csv()`. No new library rules: the rows are the same
`payout_due()` / `payout_overdue()` rows the text lists print,
projected with `--as-of` (and `--within-days` for `due`). Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    PAY_RUN_CSV_COLUMNS,
    pay_run_csv,
    payout_due,
    payout_overdue,
)


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _split_freelancer(db, project, name="Ali R.", split_pct=70.0,
                      paid_cents=90_000, payout_cents=25_000,
                      payout_paid_at=None, cadence="weekly", day=3):
    """Freelancer with one attributed invoice, `paid_cents` collected,
    `payout_cents` already paid out (on `payout_paid_at` when given),
    and a payout schedule."""
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, "USD",
                         paid_at=payout_paid_at)
    return fid


def _no_split_freelancer(db, project, name="Sana K.",
                         paid_cents=80_000, cadence="monthly", day=28):
    """Freelancer with no revenue split: the pay run reports what the
    agency still holds for them, labeled as available."""
    fid = db.add_freelancer(name, payout_cadence=cadence,
                            payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, paid_cents, method="bank")
    return fid


def _due_csv_rows(*extra):
    res = CliRunner().invoke(cli, ["payout", "due", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _overdue_csv_rows(*extra):
    res = CliRunner().invoke(cli, ["payout", "overdue", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


# 2026-10-28 is a Wednesday (ISO weekday 3).


def test_due_csv_header_and_money_columns(db, sample_project):
    _split_freelancer(db, sample_project)  # weekly Wed, due today
    rows = _due_csv_rows("--as-of", "2026-10-28")
    assert rows[0] == PAY_RUN_CSV_COLUMNS
    assert len(rows) == 2  # header + one due row
    ali = rows[1]
    assert ali[0] == "1"  # freelancer id
    assert ali[1] == "Ali R."
    assert ali[2] == "USD"
    assert ali[3] == "380.00"  # 63,000 share minus 25,000 paid
    assert ali[4] == "650.00"  # 90,000 collected minus 25,000 paid
    assert ali[5] == "weekly" and ali[6] == "3"
    assert ali[7] == "2026-10-28"  # next payout date
    assert ali[8] == ""  # due rows have no missed date


def test_due_csv_no_split_row(db, sample_project):
    # Monthly day 28, due on 2026-10-28, no split: no suggestion to
    # print, so the suggested cell stays empty and the still-held
    # balance is the amount in the available column.
    _no_split_freelancer(db, sample_project)
    rows = _due_csv_rows("--as-of", "2026-10-28")
    sana = rows[1]
    assert sana[1] == "Sana K."
    assert sana[3] == ""  # no split: nothing suggested
    assert sana[4] == "800.00"  # 80,000 collected, nothing paid out
    assert sana[5] == "monthly" and sana[6] == "28"
    assert sana[7] == "2026-10-28"


def test_due_csv_within_days(db, sample_project):
    # Weekly Friday: next payout 2026-10-30, not due on the 28th.
    _split_freelancer(db, sample_project, cadence="weekly", day=5)
    assert _due_csv_rows("--as-of", "2026-10-28") == [
        PAY_RUN_CSV_COLUMNS]
    rows = _due_csv_rows("--as-of", "2026-10-28", "--within-days", "7")
    assert len(rows) == 2
    assert rows[1][7] == "2026-10-30"


def test_due_csv_as_of_projection(db, sample_project):
    # Same book, the day after the pay run: weekly Wed's next run is
    # 2026-11-04, so the pay run is empty — the export is header-only.
    _split_freelancer(db, sample_project)  # weekly Wed
    assert _due_csv_rows("--as-of", "2026-10-29") == [
        PAY_RUN_CSV_COLUMNS]


def test_due_csv_freelancer_filter(db, sample_project):
    ali = _split_freelancer(db, sample_project)  # weekly Wed
    _split_freelancer(db, sample_project, name="Omar T.",
                      cadence="weekly", day=3)
    rows = _due_csv_rows("--freelancer", str(ali), "--as-of",
                         "2026-10-28")
    assert [r[1] for r in rows[1:]] == ["Ali R."]


def test_due_csv_file_path_writes_file_and_text_list(
        db, sample_project, tmp_path):
    _split_freelancer(db, sample_project)
    path = tmp_path / "pay-run.csv"
    res = CliRunner().invoke(cli, ["payout", "due", "--as-of",
                                   "2026-10-28", "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "Ali R." in res.output  # text list still prints
    assert f"wrote payout due CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == PAY_RUN_CSV_COLUMNS
    assert rows[1][1] == "Ali R." and rows[1][3] == "380.00"


def test_due_csv_stdout_replaces_text_list(db, sample_project):
    _split_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["payout", "due", "--as-of",
                                   "2026-10-28", "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "freelancer_id,freelancer,currency,suggested,available,")
    assert "due 2026-10-28" not in res.output  # no text list line


def test_due_csv_empty_pay_run(db, sample_project, tmp_path):
    # Settled freelancer with a due date: nothing to pay, so the
    # export is a header-only CSV in both modes.
    _split_freelancer(db, sample_project, payout_cents=63_000)
    assert _due_csv_rows("--as-of", "2026-10-28") == [
        PAY_RUN_CSV_COLUMNS]
    path = tmp_path / "pay-run.csv"
    res = CliRunner().invoke(cli, ["payout", "due", "--as-of",
                                   "2026-10-28", "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "no payouts due" in res.output
    assert f"wrote payout due CSV: {path} (0 rows)" in res.output
    assert list(csv.reader(io.StringIO(path.read_text()))) == [
        PAY_RUN_CSV_COLUMNS]


def test_due_csv_empty_roster_file_mode(db, sample_project, tmp_path):
    # No freelancers at all: header-only CSV, the roster line, and
    # the note — the export must not fail on an empty roster.
    path = tmp_path / "pay-run.csv"
    res = CliRunner().invoke(cli, ["payout", "due", "--csv",
                                   str(path)])
    assert res.exit_code == 0, res.output
    assert "no freelancers on the roster" in res.output
    assert f"wrote payout due CSV: {path} (0 rows)" in res.output
    assert list(csv.reader(io.StringIO(path.read_text()))) == [
        PAY_RUN_CSV_COLUMNS]


def test_due_csv_invalid_as_of_is_clean_error(db, sample_project):
    _split_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["payout", "due", "--as-of",
                                   "2026-13-40", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_due_csv_unknown_freelancer_is_clean_error(db, sample_project):
    res = CliRunner().invoke(cli, ["payout", "due", "--freelancer",
                                   "99", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "no freelancer #99" in res.output


def test_overdue_csv_header_and_money_columns(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    _split_freelancer(db, sample_project, payout_cents=0,
                      cadence="monthly", day=15)
    rows = _overdue_csv_rows("--as-of", "2026-10-31")
    assert rows[0] == PAY_RUN_CSV_COLUMNS
    assert len(rows) == 2  # header + one overdue row
    ali = rows[1]
    assert ali[0] == "1"
    assert ali[1] == "Ali R."
    assert ali[2] == "USD"
    assert ali[3] == "630.00"  # 70% of 90,000 collected, none paid
    assert ali[4] == "900.00"  # 90,000 collected, nothing paid out
    assert ali[5] == "monthly" and ali[6] == "15"
    assert ali[7] == "2026-11-15"  # next payout date
    assert ali[8] == "2026-10-15"  # the missed run date


def test_overdue_csv_no_split_row(db, sample_project):
    _no_split_freelancer(db, sample_project, cadence="monthly",
                         day=15)
    rows = _overdue_csv_rows("--as-of", "2026-10-31")
    sana = rows[1]
    assert sana[1] == "Sana K."
    assert sana[3] == ""  # no split: nothing suggested
    assert sana[4] == "800.00"
    assert sana[7] == "2026-11-15" and sana[8] == "2026-10-15"


def test_overdue_csv_due_today_is_not_overdue(db, sample_project):
    # Weekly Wed, due 2026-10-28 itself: in `payout due`, never here.
    _split_freelancer(db, sample_project)  # weekly Wed, due today
    assert _overdue_csv_rows("--as-of", "2026-10-28") == [
        PAY_RUN_CSV_COLUMNS]


def test_overdue_csv_paid_at_missed_run_excluded(db, sample_project):
    # Paid 25k on the missed Oct 15 run; the remainder waits for the
    # next run, so the missed run is not overdue at all.
    _split_freelancer(db, sample_project, payout_cents=25_000,
                      payout_paid_at="2026-10-15", cadence="monthly",
                      day=15)
    assert _overdue_csv_rows("--as-of", "2026-10-31") == [
        PAY_RUN_CSV_COLUMNS]


def test_overdue_csv_freelancer_filter(db, sample_project):
    ali = _split_freelancer(db, sample_project, payout_cents=0,
                            cadence="monthly", day=15)
    _split_freelancer(db, sample_project, name="Omar T.",
                      payout_cents=0, cadence="monthly", day=15)
    rows = _overdue_csv_rows("--freelancer", str(ali), "--as-of",
                             "2026-10-31")
    assert [r[1] for r in rows[1:]] == ["Ali R."]
    assert rows[1][8] == "2026-10-15"


def test_overdue_csv_file_path_writes_file_and_text_list(
        db, sample_project, tmp_path):
    _split_freelancer(db, sample_project, payout_cents=0,
                      cadence="monthly", day=15)
    path = tmp_path / "missed-runs.csv"
    res = CliRunner().invoke(cli, ["payout", "overdue", "--as-of",
                                   "2026-10-31", "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "Ali R." in res.output  # text list still prints
    assert f"wrote payout overdue CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == PAY_RUN_CSV_COLUMNS
    assert rows[1][1] == "Ali R." and rows[1][8] == "2026-10-15"


def test_overdue_csv_empty_missed_run(db, sample_project):
    # No scheduled freelancers: the export is a header-only CSV.
    assert _overdue_csv_rows("--as-of", "2026-10-31") == [
        PAY_RUN_CSV_COLUMNS]


def test_overdue_csv_invalid_as_of_is_clean_error(db, sample_project):
    _split_freelancer(db, sample_project, payout_cents=0,
                      cadence="monthly", day=15)
    res = CliRunner().invoke(cli, ["payout", "overdue", "--as-of",
                                   "2026-13-40", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_pay_run_csv_bare_library_call(db, sample_project):
    # One shared call for both lists: due rows carry an empty missed
    # cell, overdue rows carry both dates — no invented dates either
    # way, and a formula-looking freelancer name is neutralized.
    _split_freelancer(db, sample_project)  # weekly Wed, due 2026-10-28
    _split_freelancer(db, sample_project, name="=Evil",
                      payout_cents=0, cadence="monthly", day=15)
    due = list(csv.reader(io.StringIO(
        pay_run_csv(payout_due(db, as_of="2026-10-28")))))
    assert due[0] == PAY_RUN_CSV_COLUMNS
    assert len(due) == 2  # only the weekly Wed freelancer is due
    assert due[1][1] == "Ali R."
    assert due[1][8] == ""  # due rows: no missed date
    overdue = list(csv.reader(io.StringIO(
        pay_run_csv(payout_overdue(db, as_of="2026-10-31")))))
    assert overdue[0] == PAY_RUN_CSV_COLUMNS
    evil = [r for r in overdue[1:] if r[1].lstrip("'") == "=Evil"]
    assert len(evil) == 1
    assert evil[0][1] == "'=Evil"  # formula injection neutralized
    assert evil[0][7] == "2026-11-15"
    assert evil[0][8] == "2026-10-15"
