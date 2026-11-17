"""Agency-mode slice #28: CSV export for `payout list`.

`payout list` is the payout ledger — what the agency already sent,
newest first — and it was the last agency view with no spreadsheet
export: the invoice report got `--csv` in 0.31.0 and `--payments-csv`
in 0.32.0, the whole-book view got `--csv` in 0.33.0, but the payout
ledger itself still had to be retyped from the text list. This slice
adds `--csv PATH` (or `-` for stdout) to `payout list`: one row per
payout with the same columns the text list prints, plus the same
per-freelancer payout timing in trailing `payout_status` /
`payout_date` columns — on the newest row per freelancer only,
exactly like the text list. No new library rules: the timing is the
same `payout_schedule_timing()` rollup the text lines use, projected
with `--as-of`. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    PAYOUT_CSV_COLUMNS,
    payout_schedule_timing,
    payouts_csv,
)


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _paid_freelancer(db, project, name="Ali R.", split_pct=70.0,
                     paid_cents=90_000, payout_cents=10_000,
                     payout_at="2026-10-20", cadence="weekly", day=3,
                     currency="USD"):
    """Freelancer with one attributed invoice, `paid_cents` collected,
    `payout_cents` already paid out (on `payout_at`), and a payout
    schedule."""
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid, currency)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, currency, paid_at=payout_at)
    return fid


def _upcoming_freelancer(db, project, name="Omar T."):
    """Monthly day 15, paid after the missed Oct 15 run: upcoming."""
    fid = db.add_freelancer(name, split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    return fid


def _csv_rows(*extra):
    res = CliRunner().invoke(cli, ["payout", "list", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _rows_for(rows, freelancer):
    matches = [r for r in rows[1:] if r[1] == freelancer]
    assert matches, f"no CSV row for {freelancer!r} in:\n{rows}"
    return matches


# 2026-10-28 is a Wednesday (ISO weekday 3).


def test_csv_header_and_money_columns(db, sample_project):
    _paid_freelancer(db, sample_project)  # weekly Wed
    rows = _csv_rows("--as-of", "2026-10-28")
    assert rows[0] == PAYOUT_CSV_COLUMNS
    assert len(rows) == 2  # header + one payout
    ali = rows[1]
    assert ali[0]  # payout id, any value
    assert ali[1] == "Ali R."
    assert ali[2] == "USD"
    assert ali[3] == "100.00"  # 10,000 cents paid out
    assert ali[4] == "2026-10-20T00:00:00"
    assert ali[5] == ""  # no method recorded
    assert ali[6] == ""  # no note recorded
    assert ali[7] == "due" and ali[8] == "2026-10-28"


def test_csv_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    # The payout was recorded 2026-09-20 — before the missed run —
    # so the freelancer is still overdue (a payout at or after the
    # missed run would have settled it instead).
    _paid_freelancer(db, sample_project, cadence="monthly", day=15,
                     payout_at="2026-09-20")
    rows = _csv_rows("--as-of", "2026-10-31")
    ali = rows[1]
    assert ali[7] == "overdue" and ali[8] == "2026-10-15"


def test_csv_upcoming_timing(db, sample_project):
    _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--as-of", "2026-10-28")
    omar = rows[1]
    assert omar[1] == "Omar T."
    assert omar[7] == "upcoming" and omar[8] == "2026-11-15"


def test_csv_settled_and_no_schedule_timing(db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share
    # is settled even though the schedule's next run is still ahead.
    _paid_freelancer(db, sample_project, paid_cents=90_000,
                     payout_cents=63_000, cadence="monthly", day=15)
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    rows = _csv_rows("--as-of", "2026-10-20")
    ali = _rows_for(rows, "Ali R.")[0]
    assert ali[7] == "settled" and ali[8] == ""
    nosched = _rows_for(rows, "No Sched")[0]
    assert nosched[7] == "no schedule" and nosched[8] == ""


def test_csv_timing_only_on_newest_row(db, sample_project):
    # Two payouts for the same freelancer: the timing appears once,
    # on the newest row — the older row carries empty cells, exactly
    # like the text list.
    fid = _paid_freelancer(db, sample_project)
    db.record_payout(fid, 5_000, "USD", paid_at="2026-10-15")
    rows = _csv_rows("--as-of", "2026-10-28")
    ali = _rows_for(rows, "Ali R.")
    assert len(ali) == 2
    assert ali[0][4] == "2026-10-20T00:00:00"  # newest first
    assert ali[0][7] == "due" and ali[0][8] == "2026-10-28"
    assert ali[1][4] == "2026-10-15T00:00:00"
    assert ali[1][7] == "" and ali[1][8] == ""


def test_csv_per_freelancer_timing(db, sample_project):
    _paid_freelancer(db, sample_project)  # Ali: due on 2026-10-28
    _upcoming_freelancer(db, sample_project)  # Omar: upcoming
    rows = _csv_rows("--as-of", "2026-10-28")
    ali = _rows_for(rows, "Ali R.")[0]
    assert ali[7] == "due" and ali[8] == "2026-10-28"
    omar = _rows_for(rows, "Omar T.")[0]
    assert omar[7] == "upcoming" and omar[8] == "2026-11-15"


def test_csv_timing_as_of_projection(db, sample_project):
    # Same book, two report dates: due on the pay-run date, overdue
    # after it passes — the timing is projected, not today's. The
    # payout predates the missed Oct 15 run, so it does not settle it.
    _paid_freelancer(db, sample_project, cadence="monthly", day=15,
                     payout_at="2026-09-20")
    due = _csv_rows("--as-of", "2026-10-15")[1]
    assert due[7] == "due" and due[8] == "2026-10-15"
    overdue = _csv_rows("--as-of", "2026-10-31")[1]
    assert overdue[7] == "overdue" and overdue[8] == "2026-10-15"


def test_csv_freelancer_filter(db, sample_project):
    ali = _paid_freelancer(db, sample_project)
    _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--freelancer", str(ali), "--as-of", "2026-10-28")
    assert [r[1] for r in rows[1:]] == ["Ali R."]
    assert rows[1][7] == "due"


def test_csv_file_path_writes_file_and_text_list(db, sample_project,
                                                 tmp_path):
    _paid_freelancer(db, sample_project)
    path = tmp_path / "payouts.csv"
    res = CliRunner().invoke(cli, ["payout", "list", "--as-of",
                                   "2026-10-28", "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "Ali R." in res.output  # text list still prints
    assert f"wrote payout list CSV: {path} (1 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == PAYOUT_CSV_COLUMNS
    assert rows[1][1] == "Ali R." and rows[1][7] == "due"


def test_csv_empty_ledger(db, sample_project):
    # No payouts recorded: the export is a header-only CSV, in both
    # modes — the timing lookup must not fail on an empty ledger.
    rows = _csv_rows("--as-of", "2026-10-28")
    assert rows == [PAYOUT_CSV_COLUMNS]


def test_csv_empty_ledger_file_mode(db, sample_project, tmp_path):
    path = tmp_path / "payouts.csv"
    res = CliRunner().invoke(cli, ["payout", "list", "--csv",
                                   str(path)])
    assert res.exit_code == 0, res.output
    assert f"wrote payout list CSV: {path} (0 rows)" in res.output
    assert list(csv.reader(io.StringIO(path.read_text()))) == [
        PAYOUT_CSV_COLUMNS]


def test_csv_invalid_as_of_is_clean_error(db, sample_project):
    _paid_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["payout", "list", "--as-of",
                                   "2026-13-40", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_csv_library_call_without_timing(db, sample_project):
    # payouts_csv(rows) without a timing map: both timing columns
    # stay empty, so a bare call gains no invented status.
    _paid_freelancer(db, sample_project)
    rows = db.list_payouts()
    out = list(csv.reader(io.StringIO(payouts_csv(rows))))
    assert out[0] == PAYOUT_CSV_COLUMNS
    assert len(out) == 2  # header + one payout
    assert out[1][7] == "" and out[1][8] == ""


def test_csv_library_call_with_timing(db, sample_project):
    fid = _paid_freelancer(db, sample_project)
    db.record_payout(fid, 5_000, "USD", paid_at="2026-10-15")
    rows = db.list_payouts()
    timing = payout_schedule_timing(db, as_of="2026-10-28")
    out = list(csv.reader(io.StringIO(payouts_csv(rows, timing))))
    assert out[0] == PAYOUT_CSV_COLUMNS
    assert len(out) == 3  # header + two payouts
    assert out[1][7] == "due" and out[1][8] == "2026-10-28"
    assert out[2][7] == "" and out[2][8] == ""  # older row: no timing


def test_csv_formula_injection_neutralized(db, sample_project):
    fid = db.add_freelancer("Ali R.", split_pct=70.0,
                            payout_cadence="weekly", payout_day=3)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20",
                     method="=Evil", note="@mention")
    rows = _csv_rows("--as-of", "2026-10-28")
    ali = rows[1]
    assert ali[5] == "'=Evil"
    assert ali[6] == "'@mention"
