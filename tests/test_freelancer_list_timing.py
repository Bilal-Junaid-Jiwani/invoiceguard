"""Agency-mode slice #19: payout timing on `freelancer list`.

`freelancer list` is the roster itself — name, contact, split — but
no timing: telling a due freelancer from an upcoming one meant
cross-checking `payout due` and `payout overdue`, or opening
`payout schedule`. This slice appends the same timing to each roster
line, projected with `--as-of` (default today), rolled up across the
freelancer's currencies from the exact `agency summary` rows via the
`payout_schedule_timing()` rollup `payout schedule` already uses —
no new rules. Read-only. A freelancer with no billable invoices
carries no timing phrase, matching `freelancer show` and
`payout schedule`.
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


def _list(*extra):
    return CliRunner().invoke(cli, ["freelancer", "list", *extra])


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_list_due_timing(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # weekly Wed
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "Ali R." in out.output
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
    # contact/split shape and gains the matching timing phrase.
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "No Sched" in out.output
    assert "payout no schedule" in out.output


def test_list_no_billable_no_timing(db, sample_project):
    # Schedule set, but no billable invoices: no timing phrase, just
    # the roster line as before.
    fid = db.add_freelancer("No Work", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "No Work" in out.output
    for phrase in ("payout due", "payout overdue", "payout upcoming",
                   "payout settled", "payout no schedule"):
        assert phrase not in out.output


def test_list_timing_multi_currency_rollup(db, sample_project):
    # USD overdue (missed Oct 15, still owed) and EUR upcoming (paid
    # after the missed run): the rollup is the most urgent status.
    fid = db.add_freelancer("Multi C.", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    usd = _invoice(db, sample_project, 100_000, "sent", fid, "USD")
    db.record_payment(usd, 90_000, method="bank")
    eur = _invoice(db, sample_project, 100_000, "sent", fid, "EUR")
    db.record_payment(eur, 80_000, method="bank")
    db.record_payout(fid, 10_000, "EUR", paid_at="2026-10-20")
    out = _list("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout overdue (missed 2026-10-15)" in out.output


def test_list_timing_two_overdue_currencies(db, sample_project):
    # Both currencies overdue: the phrase counts them.
    fid = db.add_freelancer("Two C.", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    for currency in ("USD", "EUR"):
        inv = _invoice(db, sample_project, 100_000, "sent", fid,
                       currency)
        db.record_payment(inv, 90_000, method="bank")
    out = _list("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout overdue (2 currencies, missed 2026-10-15)" in out.output


def test_list_timing_invalid_as_of(db, sample_project):
    fid = _split_freelancer(db, sample_project)
    out = _list("--as-of", "not-a-date")
    assert out.exit_code != 0
    assert "Traceback" not in out.output


def test_list_empty_roster_no_timing_call(db, sample_project):
    # No freelancers at all: the command prints nothing and stays
    # read-only — the timing lookup must not fail on an empty roster.
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert out.output.strip() == ""
