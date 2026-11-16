"""Agency-mode slice #27: CSV export for `agency summary`.

The whole-book view was the only major agency view with no
spreadsheet export: the invoice report got `--csv` in 0.31.0 and
`--payments-csv` in 0.32.0, but the accountant's whole-book sheet
still had to be retyped from the text summary. This slice adds
`--csv PATH` (or `-` for stdout) to `agency summary`: one row per
freelancer per currency with the same money columns the text summary
prints, plus one TOTALS row per currency carrying the same rollup
timing the TOTALS lines print — projected with `--as-of` like the
text lines. No new library rules: the rows already carry their
timing (see `agency_summary()`), so the export reads them straight
off. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    AGENCY_SUMMARY_CSV_COLUMNS,
    agency_summary,
    agency_summary_csv,
    agency_summary_totals,
)


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _split_freelancer(db, project, name="Ali R.", split_pct=70.0,
                      paid_cents=90_000, payout_cents=0,
                      cadence="weekly", day=3):
    """Freelancer with one attributed invoice, `paid_cents` collected,
    `payout_cents` already paid out, and a payout schedule."""
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, "USD")
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
    res = CliRunner().invoke(cli, ["agency", "summary", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _row(rows, freelancer):
    matches = [r for r in rows[1:] if r[1] == freelancer]
    assert matches, f"no CSV row for {freelancer!r} in:\n{rows}"
    return matches[0]


def _totals(rows, currency="USD"):
    matches = [r for r in rows[1:]
               if r[1] == "TOTALS" and r[2] == currency]
    assert matches, f"no TOTALS row for {currency!r} in:\n{rows}"
    return matches[0]


# 2026-10-28 is a Wednesday (ISO weekday 3).


def test_csv_header_and_money_columns(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # weekly Wed
    rows = _csv_rows("--as-of", "2026-10-28")
    assert rows[0] == AGENCY_SUMMARY_CSV_COLUMNS
    ali = _row(rows, "Ali R.")
    assert ali[0] == str(fid)
    assert ali[2] == "USD"
    assert ali[3] == "70.0"
    assert ali[4] == "1000.00"   # billed
    assert ali[5] == "900.00"    # collected
    assert ali[6] == "100.00"    # outstanding
    assert ali[7] == "0.00"      # paid out
    assert ali[8] == "900.00"    # still owed
    assert ali[9] == "630.00"    # suggested (70% of 900.00)
    assert ali[10] == "due" and ali[11] == "2026-10-28"
    total = _totals(rows)
    assert total[0] == "" and total[3] == ""
    assert total[4] == "1000.00" and total[9] == "630.00"
    assert total[10] == "due" and total[11] == "2026-10-28"


def test_csv_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    _split_freelancer(db, sample_project, cadence="monthly", day=15)
    rows = _csv_rows("--as-of", "2026-10-31")
    ali = _row(rows, "Ali R.")
    assert ali[10] == "overdue" and ali[11] == "2026-10-15"
    total = _totals(rows)
    assert total[10] == "overdue" and total[11] == "2026-10-15"


def test_csv_upcoming_timing(db, sample_project):
    _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--as-of", "2026-10-28")
    omar = _row(rows, "Omar T.")
    assert omar[10] == "upcoming" and omar[11] == "2026-11-15"
    assert omar[9] == "300.00"  # 50% of 800.00 minus 100.00 paid out


def test_csv_settled_and_no_schedule_timing(db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share
    # is settled even though the schedule's next run is still ahead.
    _split_freelancer(db, sample_project, paid_cents=90_000,
                      payout_cents=63_000, cadence="monthly", day=15)
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    rows = _csv_rows("--as-of", "2026-10-20")
    ali = _row(rows, "Ali R.")
    assert ali[10] == "settled" and ali[11] == ""
    assert ali[9] == ""  # no suggestion once settled
    nosched = _row(rows, "No Sched")
    assert nosched[10] == "no schedule" and nosched[11] == ""
    assert nosched[9] == "630.00"


def test_csv_per_currency_timing(db, sample_project):
    # One freelancer, two currencies: USD overdue (missed Oct 15,
    # still owed) and EUR upcoming (paid after the missed run) — each
    # row carries its own currency's status, not the rollup.
    fid = db.add_freelancer("Multi C.", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    usd = _invoice(db, sample_project, 100_000, "sent", fid, "USD")
    db.record_payment(usd, 90_000, method="bank")
    eur = _invoice(db, sample_project, 100_000, "sent", fid, "EUR")
    db.record_payment(eur, 80_000, method="bank")
    db.record_payout(fid, 10_000, "EUR", paid_at="2026-10-20")
    rows = _csv_rows("--as-of", "2026-10-31")
    multi = [r for r in rows[1:] if r[1] == "Multi C."]
    assert len(multi) == 2
    by_cur = {r[2]: r for r in multi}
    assert by_cur["USD"][10] == "overdue"
    assert by_cur["USD"][11] == "2026-10-15"
    assert by_cur["EUR"][10] == "upcoming"
    assert by_cur["EUR"][11] == "2026-11-15"
    assert _totals(rows, "USD")[10] == "overdue"
    assert _totals(rows, "EUR")[10] == "upcoming"


def test_csv_no_billable_row(db, sample_project):
    fid = db.add_freelancer("Idle I.", split_pct=70.0,
                            payout_cadence="weekly", payout_day=3)
    rows = _csv_rows("--as-of", "2026-10-28")
    idle = _row(rows, "Idle I.")
    assert idle[0] == str(fid)
    assert idle[2] == ""  # no currency: no billable invoices
    assert idle[4] == idle[5] == idle[6] == "0.00"
    assert idle[9] == ""  # no suggestion
    assert idle[10] == "" and idle[11] == ""
    # No-billable freelancers contribute nothing to the totals, so
    # with no currency at all there is no TOTALS row — same as the
    # text summary, whose TOTALS section is empty here.
    assert [r for r in rows[1:] if r[1] == "TOTALS"] == []


def test_csv_totals_mixed_status_empty(db, sample_project):
    # Settled + no-schedule in the same currency: no single status
    # describes it, so the TOTALS row carries empty timing cells
    # rather than a misleading one.
    _split_freelancer(db, sample_project, paid_cents=90_000,
                      payout_cents=63_000, cadence="monthly", day=15)
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    rows = _csv_rows("--as-of", "2026-10-20")
    total = _totals(rows)
    assert total[4] == "2000.00" and total[9] == "630.00"
    assert total[10] == "" and total[11] == ""


def test_csv_timing_as_of_projection(db, sample_project):
    # Same book, two report dates: due on the pay-run date, overdue
    # after it passes — the timing is projected, not today's.
    _split_freelancer(db, sample_project, cadence="monthly", day=15)
    due = _row(_csv_rows("--as-of", "2026-10-15"), "Ali R.")
    assert due[10] == "due" and due[11] == "2026-10-15"
    overdue = _row(_csv_rows("--as-of", "2026-10-31"), "Ali R.")
    assert overdue[10] == "overdue" and overdue[11] == "2026-10-15"


def test_csv_freelancer_filter(db, sample_project):
    ali = _split_freelancer(db, sample_project)
    _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--freelancer", str(ali), "--as-of", "2026-10-28")
    assert [r[1] for r in rows[1:]] == ["Ali R.", "TOTALS"]
    assert _totals(rows)[4] == "1000.00"


def test_csv_file_path_writes_file_and_text_report(db, sample_project,
                                                   tmp_path):
    _split_freelancer(db, sample_project)
    path = tmp_path / "summary.csv"
    res = CliRunner().invoke(cli, ["agency", "summary", "--as-of",
                                   "2026-10-28", "--csv", str(path)])
    assert res.exit_code == 0, res.output
    assert "Ali R." in res.output  # text report still prints
    assert f"wrote agency summary CSV: {path} (2 rows)" in res.output
    rows = list(csv.reader(io.StringIO(path.read_text())))
    assert rows[0] == AGENCY_SUMMARY_CSV_COLUMNS
    assert _row(rows, "Ali R.")[10] == "due"


def test_csv_empty_roster(db):
    res = CliRunner().invoke(cli, ["agency", "summary", "--csv", "-"])
    assert res.exit_code == 0, res.output
    assert res.output == "no freelancers on the roster\n"


def test_csv_invalid_as_of_is_clean_error(db, sample_project):
    _split_freelancer(db, sample_project)
    res = CliRunner().invoke(cli, ["agency", "summary", "--as-of",
                                   "2026-13-40", "--csv", "-"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output


def test_csv_library_call_without_totals(db, sample_project):
    # agency_summary_csv(rows) without totals: no TOTALS rows, but the
    # timing columns are still populated — the rows carry their own
    # timing, so no timing map is needed.
    _split_freelancer(db, sample_project)
    rows = agency_summary(db, as_of="2026-10-28")
    out = list(csv.reader(io.StringIO(agency_summary_csv(rows))))
    assert out[0] == AGENCY_SUMMARY_CSV_COLUMNS
    assert len(out) == 2  # header + Ali, no TOTALS row
    assert out[1][10] == "due" and out[1][11] == "2026-10-28"


def test_csv_library_call_with_totals(db, sample_project):
    _split_freelancer(db, sample_project)
    rows = agency_summary(db, as_of="2026-10-28")
    totals = agency_summary_totals(db, as_of="2026-10-28")
    out = list(csv.reader(io.StringIO(agency_summary_csv(rows, totals))))
    assert len(out) == 3  # header + Ali + TOTALS
    assert out[2][1] == "TOTALS" and out[2][10] == "due"


def test_csv_formula_injection_neutralized(db, sample_project):
    fid = db.add_freelancer("=Evil", split_pct=70.0)
    _invoice(db, sample_project, 100_000, "sent", fid)
    rows = _csv_rows("--as-of", "2026-10-28")
    evil = _row(rows, "'=Evil")
    assert evil[1] == "'=Evil"
