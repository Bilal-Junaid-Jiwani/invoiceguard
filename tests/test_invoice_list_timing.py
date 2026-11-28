"""Agency-mode slice #39: payout timing on `invoice list`.

Slices #20–#38 put payout timing on every agency view and CSV
export — except the core `invoice list` itself: the one list an
agency operator opens first to see the whole book showed the
attributed freelancer but not whether that freelancer needs paying
now. This slice adds `--as-of DATE` to `invoice list` and appends
the same per-(freelancer, currency) timing to each text line and to
the CSV export as trailing `payout_status` / `payout_date` columns,
taken from the same `agency summary` rows the invoice report joins
onto its rows — no new rules. Read-only. Unassigned invoices and
freelancers with no billable invoices carry no timing phrase and
empty CSV cells.
"""

from __future__ import annotations

import csv
import io

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import (
    INVOICE_LIST_CSV_COLUMNS,
    agency_summary,
    invoice_list_csv,
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


def _list(*extra):
    return CliRunner().invoke(cli, ["invoice", "list", *extra])


def _list_csv_rows(db, *extra):
    res = CliRunner().invoke(cli, ["invoice", "list", *extra,
                                   "--csv", "-"])
    assert res.exit_code == 0, res.output
    return list(csv.reader(io.StringIO(res.output)))


def _timing_map(db, as_of=None):
    return {(r["freelancer_id"], r["currency"]): r
            for r in agency_summary(db, as_of=as_of)
            if r["currency"]}


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_list_due_timing(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # weekly Wed
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "[Ali R.]" in out.output
    assert "payout due (next payout 2026-10-28)" in out.output


def test_list_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)
    out = _list("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout overdue (missed 2026-10-15)" in out.output


def test_list_upcoming_timing(db, sample_project):
    # Monthly day 15, as of 2026-11-10: the Oct 15 run was missed, but a
    # payout dated after it (Oct 20) means the run is not still owed —
    # the share still due waits for the next run, Nov 15, still ahead.
    fid = db.add_freelancer("Omar T.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    out = _list("--as-of", "2026-11-10")
    assert out.exit_code == 0, out.output
    assert "payout upcoming (next payout 2026-11-15)" in out.output


def test_list_settled_timing(db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share is
    # settled even though the schedule's next run is still ahead.
    fid = _split_freelancer(db, sample_project, paid_cents=90_000,
                            payout_cents=63_000, cadence="monthly",
                            day=15)
    out = _list("--as-of", "2026-10-20")
    assert out.exit_code == 0, out.output
    assert "payout settled" in out.output


def test_list_no_schedule_timing(db, sample_project):
    # No schedule set, but billable invoices: the line keeps its
    # shape and gains the matching timing phrase.
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "No Sched" in out.output
    assert "payout no schedule" in out.output


def test_list_unassigned_invoice_no_timing(db, sample_project):
    # An unassigned invoice has no freelancer to time: the line
    # prints exactly as before, with no timing phrase.
    _invoice(db, sample_project, 100_000, "sent")
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    for phrase in ("payout due", "payout overdue", "payout upcoming",
                   "payout settled", "payout no schedule"):
        assert phrase not in out.output


def test_list_draft_only_freelancer_no_timing(db, sample_project):
    # A freelancer whose only invoice is a draft has no billable
    # invoices, so no timing entry: the line carries no phrase.
    fid = db.add_freelancer("Draft Only", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    _invoice(db, sample_project, 100_000, "draft", fid)
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "Draft Only" in out.output
    for phrase in ("payout due", "payout overdue", "payout upcoming",
                   "payout settled", "payout no schedule"):
        assert phrase not in out.output


def test_list_timing_as_of_projection(db, sample_project):
    # Monthly day 15, with a payout recorded after the missed Oct 15
    # run: as of Oct 31 the share still due waits for the next run
    # (upcoming, Nov 15); on Nov 15 itself it is due.
    fid = db.add_freelancer("Omar T.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    out = _list("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout upcoming (next payout 2026-11-15)" in out.output
    out = _list("--as-of", "2026-11-15")
    assert out.exit_code == 0, out.output
    assert "payout due (next payout 2026-11-15)" in out.output


def test_list_timing_invalid_as_of_is_clean_error(db, sample_project):
    _invoice(db, sample_project, 100_000, "sent")
    out = _list("--as-of", "not-a-date")
    assert out.exit_code != 0
    assert "Traceback" not in out.output
    assert "bad --as-of date 'not-a-date'" in out.output


def test_list_csv_timing_columns(db, sample_project):
    # Per-row join: each invoice takes its own freelancer's status in
    # its own currency; the unassigned row carries empty cells.
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)  # overdue as of 2026-10-31
    other = db.add_freelancer("Sara K.", split_pct=50.0,
                              payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", other)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(other, 10_000, "USD", paid_at="2026-10-20")
    _invoice(db, sample_project, 50_000, "sent")  # unassigned
    rows = _list_csv_rows(db, "--as-of", "2026-10-31")
    assert rows[0] == INVOICE_LIST_CSV_COLUMNS
    assert rows[0][-2:] == ["payout_status", "payout_date"]
    assert len(rows) == 4  # header + three invoice rows
    by_id = {r[0]: r for r in rows[1:]}
    assert by_id["1"][-2:] == ["overdue", "2026-10-15"]
    assert by_id["2"][-2:] == ["upcoming", "2026-11-15"]
    assert by_id["3"][-2:] == ["", ""]


def test_list_csv_timing_as_of_projection(db, sample_project):
    # Same upcoming → due projection as the text view, in the CSV.
    fid = db.add_freelancer("Omar T.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    rows = _list_csv_rows(db, "--as-of", "2026-10-31")
    assert rows[1][-2:] == ["upcoming", "2026-11-15"]
    rows = _list_csv_rows(db, "--as-of", "2026-11-15")
    assert rows[1][-2:] == ["due", "2026-11-15"]


def test_list_csv_bare_library_call_with_and_without_timing(
        db, sample_project):
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)
    rows = db.list_invoices()
    bare = list(csv.reader(io.StringIO(invoice_list_csv(rows))))
    assert bare[0] == INVOICE_LIST_CSV_COLUMNS
    assert bare[1][-2:] == ["", ""]  # no timing map: empty cells
    timed = list(csv.reader(io.StringIO(
        invoice_list_csv(rows, _timing_map(db, "2026-10-31")))))
    assert timed[1][-2:] == ["overdue", "2026-10-15"]


def test_list_timing_freelancer_filter_composes(db, sample_project):
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)
    other = db.add_freelancer("Sara K.", split_pct=50.0)
    _invoice(db, sample_project, 50_000, "sent", other)
    out = _list("--freelancer", str(fid), "--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "Ali R." in out.output
    assert "Sara K." not in out.output
    assert "payout overdue (missed 2026-10-15)" in out.output
    rows = _list_csv_rows(db, "--freelancer", str(fid),
                          "--as-of", "2026-10-31")
    assert len(rows) == 2  # header + the one attributed invoice
    assert rows[1][-2:] == ["overdue", "2026-10-15"]
