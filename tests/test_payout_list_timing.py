"""Agency-mode slice #21: payout timing on `payout list`.

`payout list` is the payout ledger — what the agency already sent,
newest first — but no timing: telling whether a freelancer in the
ledger needs paying again now meant cross-checking `payout due` and
`payout overdue`, or opening `agency summary`. This slice appends the
same per-freelancer timing rollup `payout schedule` and `freelancer
list` use, projected with `--as-of` (default today), once per
freelancer on their newest payout row — no new rules. Read-only. A
freelancer with no billable invoices carries no timing phrase,
matching `freelancer show` and `payout schedule`.
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


def _list(*extra):
    return CliRunner().invoke(cli, ["payout", "list", *extra])


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_list_due_timing(db, sample_project):
    _paid_freelancer(db, sample_project)  # weekly Wed
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "Ali R." in out.output
    assert "payout due (next payout 2026-10-28)" in out.output


def test_list_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    # The recorded payout (Oct 10) predates the missed run, so the run
    # is still owed — a payout after Oct 15 would clear it.
    _paid_freelancer(db, sample_project, cadence="monthly", day=15,
                     payout_at="2026-10-10")
    out = _list("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout overdue (missed 2026-10-15)" in out.output


def test_list_upcoming_timing(db, sample_project):
    # Monthly day 15, as of 2026-11-10: the Oct 15 run was missed, but a
    # payout dated after it (Oct 20) means the run is not still owed —
    # the share still due waits for the next run, Nov 15, still ahead.
    _paid_freelancer(db, sample_project, cadence="monthly", day=15,
                     payout_at="2026-10-20")
    out = _list("--as-of", "2026-11-10")
    assert out.exit_code == 0, out.output
    assert "payout upcoming (next payout 2026-11-15)" in out.output


def test_list_settled_timing(db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share is
    # settled even though the schedule's next run is still ahead.
    _paid_freelancer(db, sample_project, paid_cents=90_000,
                     payout_cents=63_000, cadence="monthly", day=15)
    out = _list("--as-of", "2026-10-20")
    assert out.exit_code == 0, out.output
    assert "payout settled" in out.output


def test_list_no_schedule_timing(db, sample_project):
    # No schedule set, but billable invoices: the payout line keeps its
    # ledger shape and gains the matching timing phrase.
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "No Sched" in out.output
    assert "payout no schedule" in out.output


def test_list_no_billable_no_timing(db, sample_project):
    # Schedule set and a payout recorded — but the only attributed
    # invoice was voided afterwards, so there are no billable invoices
    # left: the ledger row prints with no timing phrase rather than a
    # misleading one.
    fid = db.add_freelancer("Voided", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    db.update_invoice(iid, status="void")
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "Voided" in out.output
    for phrase in ("payout due", "payout overdue", "payout upcoming",
                   "payout settled", "payout no schedule"):
        assert phrase not in out.output


def test_list_timing_once_per_freelancer(db, sample_project):
    # Two payouts for the same freelancer: the timing phrase appears
    # exactly once, on the newest row — not repeated on the older one.
    fid = _paid_freelancer(db, sample_project, payout_at="2026-10-21")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-27")
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert out.output.count("payout due (next payout 2026-10-28)") == 1
    first_line = out.output.splitlines()[0]
    assert "2026-10-27" in first_line
    assert "payout due" in first_line
    assert "payout due" not in out.output.splitlines()[1]


def test_list_timing_multi_currency_rollup(db, sample_project):
    # USD overdue (missed Oct 15, still owed) and EUR upcoming (paid
    # after the missed run): the rollup is the most urgent status.
    fid = db.add_freelancer("Multi C.", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    usd = _invoice(db, sample_project, 100_000, "sent", fid, "USD")
    db.record_payment(usd, 90_000, method="bank")
    eur = _invoice(db, sample_project, 100_000, "sent", fid, "EUR")
    db.record_payment(eur, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-10")
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
        db.record_payout(fid, 10_000, currency, paid_at="2026-10-10")
    out = _list("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout overdue (2 currencies, missed 2026-10-15)" in out.output


def test_list_timing_as_of_projection(db, sample_project):
    # Same ledger, two report dates: upcoming before the Oct 15 run,
    # overdue after it passes unpaid. The recorded payout (Oct 10)
    # predates the missed run, so it does not clear it.
    _paid_freelancer(db, sample_project, cadence="monthly", day=15,
                     payout_at="2026-10-10")
    early = _list("--as-of", "2026-10-14")
    assert early.exit_code == 0, early.output
    assert "payout upcoming (next payout 2026-10-15)" in early.output
    late = _list("--as-of", "2026-10-31")
    assert late.exit_code == 0, late.output
    assert "payout overdue (missed 2026-10-15)" in late.output


def test_list_timing_invalid_as_of(db, sample_project):
    _paid_freelancer(db, sample_project)
    out = _list("--as-of", "not-a-date")
    assert out.exit_code != 0
    assert "Traceback" not in out.output


def test_list_empty_ledger_no_timing(db, sample_project):
    # No payouts recorded: the command prints nothing and stays
    # read-only — the timing lookup must not fail on an empty ledger.
    out = _list("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert out.output.strip() == ""


def test_list_unknown_freelancer(db, sample_project):
    out = _list("--freelancer", "999")
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output


def test_list_freelancer_filter_timing(db, sample_project):
    # --freelancer restricts both the rows and the timing to that
    # freelancer; the other freelancer's ledger rows stay out.
    fid = _paid_freelancer(db, sample_project, name="Ali R.",
                           cadence="monthly", day=15,
                           payout_at="2026-10-10")
    _paid_freelancer(db, sample_project, name="Omar T.",
                     cadence="weekly", day=3)
    out = _list("--freelancer", str(fid), "--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "Ali R." in out.output
    assert "Omar T." not in out.output
    assert "payout overdue (missed 2026-10-15)" in out.output
