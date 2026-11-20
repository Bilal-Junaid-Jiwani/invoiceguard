"""Agency-mode slice #31: CSV export for `payout schedule`.

`payout schedule` was the last agency payout view with no spreadsheet
export: the invoice report got `--csv` in 0.31.0 and `--payments-csv`
in 0.32.0, the whole-book view in 0.33.0, the payout ledger in 0.34.0,
the pay-run lists in 0.35.0, and the suggestions in 0.36.0 — but the
"who gets paid when" roster itself still had to be retyped from the
text lines. This slice adds `--csv PATH` (or `-` for stdout) to
`payout schedule`: one row per roster freelancer with the same columns
the text lines print, plus the same timing in trailing
`payout_status` / `payout_date` columns, through one shared library
call `reports.payout_schedule_csv()`. No new library rules: the rows
are the same `payout_schedule()` rows the text list prints, and the
timing is the same `payout_schedule_timing()` map, projected with
`--as-of`. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    PAYOUT_SCHEDULE_CSV_COLUMNS,
    payout_schedule,
    payout_schedule_csv,
    payout_schedule_timing,
)


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _scheduled_freelancer(db, project, name="Ali R.", split_pct=70.0,
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


def _schedule_csv_rows(*extra):
    res = CliRunner().invoke(cli, ["payout", "schedule", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


# 2026-10-28 is a Wednesday (ISO weekday 3).


def test_schedule_csv_header_and_columns(db, sample_project):
    _scheduled_freelancer(db, sample_project)  # weekly Wed, due today
    rows = _schedule_csv_rows("--as-of", "2026-10-28")
    assert rows[0] == PAYOUT_SCHEDULE_CSV_COLUMNS
    assert len(rows) == 2  # header + one schedule row
    ali = rows[1]
    assert ali[0] == "1"  # freelancer id
    assert ali[1] == "Ali R."
    assert ali[2] == "weekly"
    assert ali[3] == "3"  # ISO weekday
    assert ali[4] == "2026-10-28"  # next payout date
    assert ali[5] == "due"
    assert ali[6] == "2026-10-28"  # next payout date


def test_schedule_csv_no_schedule_row(db, sample_project):
    # No schedule set: the row still exports, with the schedule
    # columns and the next date empty, and the no-schedule status
    # with an empty date rather than an invented one.
    fid = db.add_freelancer("Nadia B.", split_pct=50.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    rows = _schedule_csv_rows("--as-of", "2026-10-28")
    nadia = rows[1]
    assert nadia[1] == "Nadia B."
    assert nadia[2] == ""  # no cadence
    assert nadia[3] == ""  # no day
    assert nadia[4] == ""  # no next payout date
    assert nadia[5] == "no schedule"
    assert nadia[6] == ""


def test_schedule_csv_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    _scheduled_freelancer(db, sample_project, payout_cents=0,
                          cadence="monthly", day=15)
    rows = _schedule_csv_rows("--as-of", "2026-10-31")
    ali = rows[1]
    assert ali[2] == "monthly"
    assert ali[3] == "15"
    assert ali[4] == "2026-11-15"  # next payout date
    assert ali[5] == "overdue"
    assert ali[6] == "2026-10-15"  # the missed run date


def test_schedule_csv_upcoming_timing(db, sample_project):
    # Monthly day 15, as of 2026-11-10: the Oct 15 run was missed, but
    # a payout dated after it (Oct 20) means the run is not still owed
    # — the share still due waits for the next run, Nov 15.
    fid = db.add_freelancer("Omar T.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    rows = _schedule_csv_rows("--as-of", "2026-11-10")
    omar = rows[1]
    assert omar[1] == "Omar T."
    assert omar[4] == "2026-11-15"
    assert omar[5] == "upcoming"
    assert omar[6] == "2026-11-15"


def test_schedule_csv_settled_timing(db, sample_project):
    # The whole still-due share already paid out: settled, with an
    # empty date rather than a next run that needs no payment.
    _scheduled_freelancer(db, sample_project, payout_cents=63_000)
    rows = _schedule_csv_rows("--as-of", "2026-10-28")
    ali = rows[1]
    assert ali[4] == "2026-10-28"
    assert ali[5] == "settled"
    assert ali[6] == ""


def test_schedule_csv_no_billable_row(db, sample_project):
    # A scheduled freelancer with no billable invoices has no timing
    # entry: both timing cells stay empty rather than carrying a
    # misleading status, exactly like the text list's missing phrase.
    db.add_freelancer("Noor F.", payout_cadence="weekly", payout_day=3)
    rows = _schedule_csv_rows("--as-of", "2026-10-28")
    noor = rows[1]
    assert noor[1] == "Noor F."
    assert noor[2] == "weekly"
    assert noor[4] == "2026-10-28"
    assert noor[5] == ""
    assert noor[6] == ""


def test_schedule_csv_as_of_projection(db, sample_project):
    # Same book, projected to 2026-11-10: weekly Wed's next run is
    # Nov 11, so the schedule is upcoming, not due.
    _scheduled_freelancer(db, sample_project)  # weekly Wed
    rows = _schedule_csv_rows("--as-of", "2026-11-10")
    assert rows[1][4] == "2026-11-11"
    assert rows[1][5] == "upcoming"
    assert rows[1][6] == "2026-11-11"


def test_schedule_csv_freelancer_filter(db, sample_project):
    ali = _scheduled_freelancer(db, sample_project)  # weekly Wed
    _scheduled_freelancer(db, sample_project, name="Omar T.",
                          cadence="weekly", day=3)
    rows = _schedule_csv_rows("--freelancer", str(ali), "--as-of",
                             "2026-10-28")
    assert [r[1] for r in rows[1:]] == ["Ali R."]


def test_schedule_csv_file_path_writes_file_and_text_list(
        db, sample_project, tmp_path):
    _scheduled_freelancer(db, sample_project)
    path = tmp_path / "schedules.csv"
    res = CliRunner().invoke(cli, ["payout", "schedule", "--as-of",
                                   "2026-10-28", "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "Ali R." in res.output  # text list still prints
    assert f"wrote payout schedule CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == PAYOUT_SCHEDULE_CSV_COLUMNS
    assert rows[1][1] == "Ali R." and rows[1][4] == "2026-10-28"


def test_schedule_csv_stdout_replaces_text_list(db, sample_project):
    _scheduled_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["payout", "schedule", "--as-of",
                                   "2026-10-28", "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "freelancer_id,freelancer,payout_cadence,payout_day,")
    assert "payout due" not in res.output  # no text list line


def test_schedule_csv_empty_roster(db, sample_project, tmp_path):
    # No freelancers at all: header-only CSV in both modes.
    assert _schedule_csv_rows() == [PAYOUT_SCHEDULE_CSV_COLUMNS]
    path = tmp_path / "schedules.csv"
    res = CliRunner().invoke(cli, ["payout", "schedule", "--csv",
                                   str(path)])
    assert res.exit_code == 0, res.output
    assert "no freelancers on the roster" in res.output
    assert f"wrote payout schedule CSV: {path} (0 rows)" in res.output
    assert list(csv.reader(io.StringIO(path.read_text()))) == [
        PAYOUT_SCHEDULE_CSV_COLUMNS]


def test_schedule_csv_invalid_as_of_is_clean_error(db, sample_project):
    _scheduled_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["payout", "schedule", "--as-of",
                                   "2026-13-40", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_schedule_csv_unknown_freelancer_is_clean_error(db,
                                                        sample_project):
    res = CliRunner().invoke(cli, ["payout", "schedule", "--freelancer",
                                   "99", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "no freelancer #99" in res.output


def test_payout_schedule_csv_bare_library_call(db, sample_project):
    # Without a timing map both timing columns stay empty, so a bare
    # call gains no invented status; with the map they carry the same
    # timing the text list prints — and a formula-looking freelancer
    # name is neutralized. A timing entry with no status (currencies
    # disagreeing) also exports empty cells, not a guessed status.
    _scheduled_freelancer(db, sample_project,  # weekly Wed, due 2026-10-28
                          payout_paid_at="2026-10-20")
    _scheduled_freelancer(db, sample_project, name="=Evil",
                          payout_cents=0, cadence="monthly", day=15)
    rows = payout_schedule(db)
    bare = list(csv.reader(io.StringIO(payout_schedule_csv(rows))))
    assert bare[0] == PAYOUT_SCHEDULE_CSV_COLUMNS
    assert all(r[5] == "" and r[6] == "" for r in bare[1:])
    timing = payout_schedule_timing(db, as_of="2026-10-31")
    timed = list(csv.reader(io.StringIO(
        payout_schedule_csv(rows, timing))))
    assert timed[0] == PAYOUT_SCHEDULE_CSV_COLUMNS
    evil = [r for r in timed[1:] if r[1].lstrip("'") == "=Evil"]
    assert len(evil) == 1
    assert evil[0][1] == "'=Evil"  # formula injection neutralized
    assert evil[0][5] == "overdue"
    assert evil[0][6] == "2026-10-15"
    ali = [r for r in timed[1:] if r[1] == "Ali R."]
    assert len(ali) == 1
    # As of 2026-10-31 the weekly Wed run (Oct 28) was missed: overdue.
    assert ali[0][5] == "overdue"
    assert ali[0][6] == "2026-10-28"
    # No status in the map (currencies disagree) -> empty cells.
    mixed = dict(timing)
    mixed[1] = {"payout_status": None, "payout_currencies": None,
                "missed_payout_date": None, "next_payout_date": None}
    mixed_rows = list(csv.reader(io.StringIO(
        payout_schedule_csv(rows, mixed))))
    ali_mixed = [r for r in mixed_rows[1:] if r[1] == "Ali R."]
    assert ali_mixed[0][5] == "" and ali_mixed[0][6] == ""
