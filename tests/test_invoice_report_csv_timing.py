"""Agency-mode slice #25: payout timing in the `invoice report` CSV.

The text report got payout timing on its "by freelancer" lines in
0.26.0, but the `--csv` export dropped it: the accountant's
spreadsheet answered "who owes us" with no hint of "who do we owe,
and when". This slice joins the same per-(freelancer, currency)
timing the text report uses onto each CSV row — ``payout_status``
(due / overdue / upcoming / settled / no schedule) plus
``payout_date`` (the missed run date when overdue, the next payout
date when due or upcoming, empty for settled / no schedule),
projected with `--as-of` like the text lines. Unassigned invoices
carry empty cells rather than a misleading status. No new library
rules: the timing comes from the `agency_summary()` rows the CLI
already builds for the text report. Read-only.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli


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
    res = CliRunner().invoke(cli, ["invoice", "report", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _row(rows, freelancer):
    matches = [r for r in rows[1:] if r[13] == freelancer]
    assert matches, f"no CSV row for {freelancer!r} in:\n{rows}"
    return matches[0]


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_csv_due_and_upcoming_timing(db, sample_project):
    _split_freelancer(db, sample_project)  # weekly Wed
    _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--as-of", "2026-10-28")
    assert rows[0][14] == "payout_status"
    assert rows[0][15] == "payout_date"
    ali = _row(rows, "Ali R.")
    assert ali[14] == "due" and ali[15] == "2026-10-28"
    omar = _row(rows, "Omar T.")
    assert omar[14] == "upcoming" and omar[15] == "2026-11-15"


def test_csv_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    _split_freelancer(db, sample_project, cadence="monthly", day=15)
    _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--as-of", "2026-10-31")
    ali = _row(rows, "Ali R.")
    assert ali[14] == "overdue" and ali[15] == "2026-10-15"
    omar = _row(rows, "Omar T.")
    assert omar[14] == "upcoming" and omar[15] == "2026-11-15"


def test_csv_settled_and_no_schedule_timing(db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share
    # is settled even though the schedule's next run is still ahead.
    _split_freelancer(db, sample_project, paid_cents=90_000,
                      payout_cents=63_000, cadence="monthly", day=15)
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--as-of", "2026-10-20")
    ali = _row(rows, "Ali R.")
    assert ali[14] == "settled" and ali[15] == ""
    nosched = _row(rows, "No Sched")
    assert nosched[14] == "no schedule" and nosched[15] == ""


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
    multi = [r for r in rows[1:] if r[13] == "Multi C."]
    assert len(multi) == 2
    by_cur = {r[5]: r for r in multi}
    assert by_cur["USD"][14] == "overdue"
    assert by_cur["USD"][15] == "2026-10-15"
    assert by_cur["EUR"][14] == "upcoming"
    assert by_cur["EUR"][15] == "2026-11-15"


def test_csv_unassigned_no_timing(db, sample_project):
    _split_freelancer(db, sample_project)  # weekly Wed, due 2026-10-28
    _invoice(db, sample_project, 50_000, "sent")
    rows = _csv_rows("--as-of", "2026-10-28")
    blank = [r for r in rows[1:] if r[13] == ""]
    assert len(blank) == 1
    assert blank[0][14] == "" and blank[0][15] == ""


def test_csv_timing_as_of_projection(db, sample_project):
    # Same book, two report dates: due on the pay-run date, overdue
    # after it passes — the timing is projected, not today's.
    _split_freelancer(db, sample_project, cadence="monthly", day=15)
    _upcoming_freelancer(db, sample_project)
    due = _row(_csv_rows("--as-of", "2026-10-15"), "Ali R.")
    assert due[14] == "due" and due[15] == "2026-10-15"
    overdue = _row(_csv_rows("--as-of", "2026-10-31"), "Ali R.")
    assert overdue[14] == "overdue" and overdue[15] == "2026-10-15"


def test_csv_timing_library_call_keeps_bare_rows_empty(db,
                                                       sample_project):
    # invoices_csv() without a timing map keeps the old shape apart
    # from the two new (empty) columns — existing callers that do not
    # join timing get no invented status.
    from invoiceguard.reports import invoices_csv, receivables_report

    _split_freelancer(db, sample_project)
    rep = receivables_report(db, as_of="2026-10-28")
    rows = list(csv.reader(io.StringIO(invoices_csv(rep["rows"]))))
    assert rows[0][14] == "payout_status"
    assert rows[0][15] == "payout_date"
    assert rows[1][14] == "" and rows[1][15] == ""
