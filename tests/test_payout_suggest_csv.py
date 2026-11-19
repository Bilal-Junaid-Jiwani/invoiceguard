"""Agency-mode slice #30: CSV export for `payout suggest`.

`payout suggest` was the last agency payout view with no spreadsheet
export: the invoice report got `--csv` in 0.31.0 and `--payments-csv`
in 0.32.0, the whole-book view in 0.33.0, the payout ledger in 0.34.0,
and the pay-run lists in 0.35.0 — but the "what do I pay next" list
itself still had to be retyped from the text lines. This slice adds
`--csv PATH` (or `-` for stdout) to `payout suggest`: one row per
suggestion per currency with the same columns the text lines print,
plus the same timing in trailing `payout_status` / `payout_date`
columns, through one shared library call
`reports.payout_suggestions_csv()`. No new library rules: the rows
are the same `payout_suggestions()` rows the text list prints, and
the timing is the same `payout_suggestion_timing()` map, projected
with `--as-of`. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    PAYOUT_SUGGESTIONS_CSV_COLUMNS,
    payout_suggestion_timing,
    payout_suggestions,
    payout_suggestions_csv,
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
    """Freelancer with no revenue split: the suggestion reports what
    the agency still holds for them, labeled as available."""
    fid = db.add_freelancer(name, payout_cadence=cadence,
                            payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, paid_cents, method="bank")
    return fid


def _suggest_csv_rows(*extra):
    res = CliRunner().invoke(cli, ["payout", "suggest", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


# 2026-10-28 is a Wednesday (ISO weekday 3).


def test_suggest_csv_header_and_money_columns(db, sample_project):
    _split_freelancer(db, sample_project)  # weekly Wed, due today
    rows = _suggest_csv_rows("--as-of", "2026-10-28")
    assert rows[0] == PAYOUT_SUGGESTIONS_CSV_COLUMNS
    assert len(rows) == 2  # header + one suggestion row
    ali = rows[1]
    assert ali[0] == "1"  # freelancer id
    assert ali[1] == "Ali R."
    assert ali[2] == "USD"
    assert ali[3] == "380.00"  # 63,000 share minus 25,000 paid
    assert ali[4] == "650.00"  # 90,000 collected minus 25,000 paid
    assert ali[5] == "900.00"  # collected
    assert ali[6] == "250.00"  # already paid out
    assert ali[7] == "70.0"  # split pct
    assert ali[8] == "due"
    assert ali[9] == "2026-10-28"  # next payout date


def test_suggest_csv_no_split_row(db, sample_project):
    # Monthly day 28, as of 2026-10-28: due, no split — no share to
    # suggest, so the suggested cell stays empty and the still-held
    # balance is the amount in the available column.
    _no_split_freelancer(db, sample_project)
    rows = _suggest_csv_rows("--as-of", "2026-10-28")
    sana = rows[1]
    assert sana[1] == "Sana K."
    assert sana[3] == ""  # no split: nothing suggested
    assert sana[4] == "800.00"  # 80,000 collected, nothing paid out
    assert sana[5] == "800.00"
    assert sana[6] == "0.00"
    assert sana[7] == ""  # no split set
    assert sana[8] == "due"
    assert sana[9] == "2026-10-28"


def test_suggest_csv_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    _split_freelancer(db, sample_project, payout_cents=0,
                      cadence="monthly", day=15)
    rows = _suggest_csv_rows("--as-of", "2026-10-31")
    ali = rows[1]
    assert ali[3] == "630.00"  # 70% of 90,000 collected, none paid
    assert ali[8] == "overdue"
    assert ali[9] == "2026-10-15"  # the missed run date


def test_suggest_csv_upcoming_timing(db, sample_project):
    # Monthly day 15, as of 2026-11-10: the Oct 15 run was missed, but
    # a payout dated after it (Oct 20) means the run is not still owed
    # — the share still due waits for the next run, Nov 15.
    fid = db.add_freelancer("Omar T.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    rows = _suggest_csv_rows("--as-of", "2026-11-10")
    omar = rows[1]
    assert omar[1] == "Omar T."
    assert omar[3] == "300.00"  # 40,000 share minus 10,000 paid
    assert omar[4] == "700.00"
    assert omar[8] == "upcoming"
    assert omar[9] == "2026-11-15"


def test_suggest_csv_no_schedule_timing(db, sample_project):
    # No schedule set: the suggestion still exports, with the
    # no-schedule status and an empty date rather than an invented one.
    fid = db.add_freelancer("Nadia B.", split_pct=50.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    rows = _suggest_csv_rows("--as-of", "2026-10-28")
    nadia = rows[1]
    assert nadia[1] == "Nadia B."
    assert nadia[3] == "400.00"
    assert nadia[8] == "no schedule"
    assert nadia[9] == ""


def test_suggest_csv_as_of_projection(db, sample_project):
    # Same book, projected to 2026-11-10: weekly Wed's next run is
    # Nov 11, so the suggestion is upcoming, not due.
    _split_freelancer(db, sample_project)  # weekly Wed
    rows = _suggest_csv_rows("--as-of", "2026-11-10")
    assert rows[1][8] == "upcoming"
    assert rows[1][9] == "2026-11-11"


def test_suggest_csv_freelancer_filter(db, sample_project):
    ali = _split_freelancer(db, sample_project)  # weekly Wed
    _split_freelancer(db, sample_project, name="Omar T.",
                      cadence="weekly", day=3)
    rows = _suggest_csv_rows("--freelancer", str(ali), "--as-of",
                             "2026-10-28")
    assert [r[1] for r in rows[1:]] == ["Ali R."]


def test_suggest_csv_file_path_writes_file_and_text_list(
        db, sample_project, tmp_path):
    _split_freelancer(db, sample_project)
    path = tmp_path / "suggestions.csv"
    res = CliRunner().invoke(cli, ["payout", "suggest", "--as-of",
                                   "2026-10-28", "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "Ali R." in res.output  # text list still prints
    assert f"wrote payout suggest CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == PAYOUT_SUGGESTIONS_CSV_COLUMNS
    assert rows[1][1] == "Ali R." and rows[1][3] == "380.00"


def test_suggest_csv_stdout_replaces_text_list(db, sample_project):
    _split_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["payout", "suggest", "--as-of",
                                   "2026-10-28", "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output.startswith(
        "freelancer_id,freelancer,currency,suggested,available,")
    assert "payout due" not in res.output  # no text list line


def test_suggest_csv_empty_suggestions(db, sample_project, tmp_path):
    # Settled freelancer: the share is fully paid, so there is no
    # suggestion — the export is a header-only CSV in both modes.
    _split_freelancer(db, sample_project, payout_cents=63_000)
    assert _suggest_csv_rows("--as-of", "2026-10-28") == [
        PAYOUT_SUGGESTIONS_CSV_COLUMNS]
    path = tmp_path / "suggestions.csv"
    res = CliRunner().invoke(cli, ["payout", "suggest", "--as-of",
                                   "2026-10-28", "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "no payout suggestions" in res.output
    assert f"wrote payout suggest CSV: {path} (0 rows)" in res.output
    assert list(csv.reader(io.StringIO(path.read_text()))) == [
        PAYOUT_SUGGESTIONS_CSV_COLUMNS]


def test_suggest_csv_empty_roster_file_mode(db, sample_project,
                                          tmp_path):
    # No freelancers at all: header-only CSV, the empty line, and the
    # note — the export must not fail on an empty roster.
    path = tmp_path / "suggestions.csv"
    res = CliRunner().invoke(cli, ["payout", "suggest", "--csv",
                                   str(path)])
    assert res.exit_code == 0, res.output
    assert "no payout suggestions" in res.output
    assert f"wrote payout suggest CSV: {path} (0 rows)" in res.output
    assert list(csv.reader(io.StringIO(path.read_text()))) == [
        PAYOUT_SUGGESTIONS_CSV_COLUMNS]


def test_suggest_csv_invalid_as_of_is_clean_error(db, sample_project):
    _split_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["payout", "suggest", "--as-of",
                                   "2026-13-40", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_suggest_csv_unknown_freelancer_is_clean_error(db,
                                                       sample_project):
    res = CliRunner().invoke(cli, ["payout", "suggest", "--freelancer",
                                   "99", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "no freelancer #99" in res.output


def test_payout_suggestions_csv_bare_library_call(db, sample_project):
    # Without a timing map both timing columns stay empty, so a bare
    # call gains no invented status; with the map they carry the same
    # timing the text list prints — and a formula-looking freelancer
    # name is neutralized.
    _split_freelancer(db, sample_project,  # weekly Wed, due 2026-10-28
                      payout_paid_at="2026-10-20")
    _split_freelancer(db, sample_project, name="=Evil",
                      payout_cents=0, cadence="monthly", day=15)
    rows = payout_suggestions(db)
    bare = list(csv.reader(io.StringIO(
        payout_suggestions_csv(rows))))
    assert bare[0] == PAYOUT_SUGGESTIONS_CSV_COLUMNS
    assert all(r[8] == "" and r[9] == "" for r in bare[1:])
    timing = payout_suggestion_timing(db, as_of="2026-10-31")
    timed = list(csv.reader(io.StringIO(
        payout_suggestions_csv(rows, timing))))
    assert timed[0] == PAYOUT_SUGGESTIONS_CSV_COLUMNS
    evil = [r for r in timed[1:] if r[1].lstrip("'") == "=Evil"]
    assert len(evil) == 1
    assert evil[0][1] == "'=Evil"  # formula injection neutralized
    assert evil[0][8] == "overdue"
    assert evil[0][9] == "2026-10-15"
    ali = [r for r in timed[1:] if r[1] == "Ali R."]
    assert len(ali) == 1
    # As of 2026-10-31 the weekly Wed run (Oct 28) was missed: overdue.
    assert ali[0][8] == "overdue"
    assert ali[0][9] == "2026-10-28"
