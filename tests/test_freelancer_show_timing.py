"""Agency-mode slice #16: payout timing on `freelancer show`.

The agency summary (slices #14/#15) carries per-currency payout timing
for the whole book, but drilling into one freelancer — `freelancer
show` — showed only the schedule and the next payout date, so
answering "does this freelancer need paying now" meant cross-checking
`payout due` and `payout overdue`. This slice appends the same timing
to each currency line of `freelancer show`, projected with `--as-of`
(default today), derived from the exact `payout due` / `overdue`
rules — no new rules. Read-only.
"""

from __future__ import annotations

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


def _show(fid, *extra):
    return CliRunner().invoke(cli, ["freelancer", "show", str(fid),
                                   *extra])


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_show_due_timing(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # weekly Wed
    out = _show(fid, "--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "payout due (next payout 2026-10-28)" in out.output


def test_show_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)
    out = _show(fid, "--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout overdue (missed 2026-10-15)" in out.output


def test_show_upcoming_timing(db, sample_project):
    # Monthly day 15, as of 2026-11-10: the Oct 15 run was missed, but a
    # payout dated after it (Oct 20) means the run is not still owed —
    # the share still due waits for the next run, Nov 15, still ahead.
    fid = db.add_freelancer("Omar T.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    out = _show(fid, "--as-of", "2026-11-10")
    assert out.exit_code == 0, out.output
    assert "payout upcoming (next payout 2026-11-15)" in out.output


def test_show_settled_timing(db, sample_project):
    # Due today (weekly Wed) but the full share is already paid.
    fid = _split_freelancer(db, sample_project, payout_cents=63_000)
    out = _show(fid, "--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "payout settled" in out.output


def test_show_no_schedule_timing(db, sample_project):
    fid = db.add_freelancer("Sana K.", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    out = _show(fid, "--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "payout no schedule" in out.output


def test_show_no_billable_freelancer_carries_no_timing(db):
    fid = db.add_freelancer("Idle I.", payout_cadence="weekly",
                            payout_day=3)
    out = _show(fid, "--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "(no billable invoices)" in out.output
    for phrase in ("payout due", "payout overdue", "payout upcoming",
                   "payout settled", "payout no schedule"):
        assert phrase not in out.output


def test_show_invalid_as_of_is_clean_error(db, sample_project):
    fid = _split_freelancer(db, sample_project)
    out = _show(fid, "--as-of", "not-a-date")
    assert out.exit_code != 0
    assert "Traceback" not in out.output
