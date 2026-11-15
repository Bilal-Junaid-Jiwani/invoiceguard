"""Agency-mode slice #26: payout timing in the `invoice report`
payments CSV.

The invoice CSV got payout timing in 0.31.0, but the `--payments-csv`
export dropped it: the payment ledger answered "what came in" with no
hint of "who do we owe for this work, and when". This slice joins the
same per-(freelancer, currency) timing the text report uses onto each
payment row — ``payout_status`` (due / overdue / upcoming / settled /
no schedule) plus ``payout_date`` (the missed run date when overdue,
the next payout date when due or upcoming, empty for settled / no
schedule), projected with `--as-of` like the text lines. The join is
through the payment's invoice: its attribution and its currency, so a
payment in EUR carries the freelancer's EUR timing. Payments on
unassigned invoices carry empty cells rather than a misleading status,
and payments against now-paid invoices still export — with timing.
No new library rules: the timing comes from the `agency_summary()`
rows the CLI already builds for the text report. Read-only.
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
    `payout_cents` already paid out, and a payout schedule. Returns
    (freelancer id, invoice id)."""
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, "USD")
    return fid, iid


def _upcoming_freelancer(db, project, name="Omar T."):
    """Monthly day 15, paid after the missed Oct 15 run: upcoming."""
    fid = db.add_freelancer(name, split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    return fid, iid


def _csv_rows(*extra):
    res = CliRunner().invoke(cli, ["invoice", "report", *extra,
                                   "--payments-csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _row(rows, invoice_id):
    matches = [r for r in rows[1:] if r[1] == str(invoice_id)]
    assert matches, f"no payment row for invoice #{invoice_id} in:\n{rows}"
    return matches[0]


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_payments_csv_due_and_upcoming_timing(db, sample_project):
    _, ali_iid = _split_freelancer(db, sample_project)  # weekly Wed
    _, omar_iid = _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--as-of", "2026-10-28")
    assert rows[0][9] == "payout_status"
    assert rows[0][10] == "payout_date"
    ali = _row(rows, ali_iid)
    assert ali[9] == "due" and ali[10] == "2026-10-28"
    omar = _row(rows, omar_iid)
    assert omar[9] == "upcoming" and omar[10] == "2026-11-15"


def test_payments_csv_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    _, ali_iid = _split_freelancer(db, sample_project,
                                   cadence="monthly", day=15)
    _, omar_iid = _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--as-of", "2026-10-31")
    ali = _row(rows, ali_iid)
    assert ali[9] == "overdue" and ali[10] == "2026-10-15"
    omar = _row(rows, omar_iid)
    assert omar[9] == "upcoming" and omar[10] == "2026-11-15"


def test_payments_csv_settled_and_no_schedule_timing(db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share
    # is settled even though the schedule's next run is still ahead.
    _, ali_iid = _split_freelancer(db, sample_project, paid_cents=90_000,
                                   payout_cents=63_000, cadence="monthly",
                                   day=15)
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    nosched_iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(nosched_iid, 90_000, method="bank")
    _upcoming_freelancer(db, sample_project)
    rows = _csv_rows("--as-of", "2026-10-20")
    ali = _row(rows, ali_iid)
    assert ali[9] == "settled" and ali[10] == ""
    nosched = _row(rows, nosched_iid)
    assert nosched[9] == "no schedule" and nosched[10] == ""


def test_payments_csv_per_currency_timing(db, sample_project):
    # One freelancer, two currencies: USD overdue (missed Oct 15,
    # still owed) and EUR upcoming (paid after the missed run) — each
    # payment carries its own currency's status, not the rollup.
    fid = db.add_freelancer("Multi C.", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    usd = _invoice(db, sample_project, 100_000, "sent", fid, "USD")
    db.record_payment(usd, 90_000, method="bank")
    eur = _invoice(db, sample_project, 100_000, "sent", fid, "EUR")
    db.record_payment(eur, 80_000, method="bank")
    db.record_payout(fid, 10_000, "EUR", paid_at="2026-10-20")
    rows = _csv_rows("--as-of", "2026-10-31")
    usd_row = _row(rows, usd)
    assert usd_row[7] == "USD"
    assert usd_row[9] == "overdue" and usd_row[10] == "2026-10-15"
    eur_row = _row(rows, eur)
    assert eur_row[7] == "EUR"
    assert eur_row[9] == "upcoming" and eur_row[10] == "2026-11-15"


def test_payments_csv_unassigned_no_timing(db, sample_project):
    _split_freelancer(db, sample_project)  # weekly Wed, due 2026-10-28
    iid = _invoice(db, sample_project, 50_000, "sent")
    db.record_payment(iid, 25_000, method="bank")
    rows = _csv_rows("--as-of", "2026-10-28")
    blank = _row(rows, iid)
    assert blank[9] == "" and blank[10] == ""


def test_payments_csv_timing_as_of_projection(db, sample_project):
    # Same book, two report dates: due on the pay-run date, overdue
    # after it passes — the timing is projected, not today's.
    _, ali_iid = _split_freelancer(db, sample_project,
                                   cadence="monthly", day=15)
    _upcoming_freelancer(db, sample_project)
    due = _row(_csv_rows("--as-of", "2026-10-15"), ali_iid)
    assert due[9] == "due" and due[10] == "2026-10-15"
    overdue = _row(_csv_rows("--as-of", "2026-10-31"), ali_iid)
    assert overdue[9] == "overdue" and overdue[10] == "2026-10-15"


def test_payments_csv_paid_invoice_still_carries_timing(db,
                                                        sample_project):
    # The ledger exports payments on now-paid invoices too — and
    # those rows still carry the freelancer's timing.
    _, ali_iid = _split_freelancer(db, sample_project,
                                   paid_cents=100_000,
                                   cadence="monthly", day=15)
    assert db.get_invoice(ali_iid)["status"] == "paid"
    rows = _csv_rows("--as-of", "2026-10-31")
    ali = _row(rows, ali_iid)
    assert ali[9] == "overdue" and ali[10] == "2026-10-15"


def test_payments_csv_timing_library_call_keeps_bare_rows_empty(
        db, sample_project):
    # payments_csv() without a timing map keeps the old shape apart
    # from the two new (empty) columns — existing callers that do not
    # join timing get no invented status.
    from invoiceguard.reports import payments_csv

    _, ali_iid = _split_freelancer(db, sample_project)
    rows = list(csv.reader(io.StringIO(payments_csv(db))))
    assert rows[0][9] == "payout_status"
    assert rows[0][10] == "payout_date"
    assert len(rows) == 2
    assert rows[1][1] == str(ali_iid)
    assert rows[1][9] == "" and rows[1][10] == ""
