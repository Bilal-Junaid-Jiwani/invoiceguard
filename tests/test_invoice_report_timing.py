"""Agency-mode slice #20: payout timing on `invoice report`.

`invoice report` is the receivables view — aging buckets, per-client
and per-freelancer totals over open invoices — but its "by freelancer"
lines answered only "who owes us": telling whether the agency owes
that freelancer a payout now meant cross-checking `payout due` and
`payout overdue`, or opening `agency summary`. This slice appends the
same per-currency timing the agency summary lines carry (derived from
the exact `payout due` / `overdue` rules as of `--as-of`, default
today), so each line also answers "who do we owe, and when". No new
library rules: the timing comes from the `agency_summary()` rows
themselves, and the by-freelancer entries gain only the freelancer
id (None for "unassigned") so the CLI can join the two. The
"unassigned" line carries no timing phrase. Read-only.
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


def _report(*extra):
    return CliRunner().invoke(cli, ["invoice", "report", *extra])


def _line(output, fragment):
    """The by-freelancer row containing `fragment`.

    Scoped to the "by freelancer:" section: the aging buckets above
    it print the same money strings.
    """
    section = output.split("by freelancer:", 1)[1]
    lines = [ln for ln in section.splitlines() if fragment in ln]
    assert lines, f"no by-freelancer line with {fragment!r} in:\n{output}"
    return lines[0]


def _upcoming_freelancer(db, project, name="Omar T."):
    """Monthly day 15, paid after the missed Oct 15 run: upcoming."""
    fid = db.add_freelancer(name, split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    return fid


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_report_due_and_upcoming_timing(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # weekly Wed
    _upcoming_freelancer(db, sample_project)
    out = _report("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "by freelancer:" in out.output
    assert "payout due (next payout 2026-10-28)" in \
        _line(out.output, "Ali R.")
    assert "payout upcoming (next payout 2026-11-15)" in \
        _line(out.output, "Omar T.")


def test_report_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)
    _upcoming_freelancer(db, sample_project)
    out = _report("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout overdue (missed 2026-10-15)" in \
        _line(out.output, "Ali R.")
    assert "payout upcoming (next payout 2026-11-15)" in \
        _line(out.output, "Omar T.")


def test_report_settled_timing(db, sample_project):
    # 70% of 90,000 collected is 63,000 — paid in full, so the share
    # is settled even though the schedule's next run is still ahead.
    fid = _split_freelancer(db, sample_project, paid_cents=90_000,
                            payout_cents=63_000, cadence="monthly",
                            day=15)
    _upcoming_freelancer(db, sample_project)
    out = _report("--as-of", "2026-10-20")
    assert out.exit_code == 0, out.output
    assert "payout settled" in _line(out.output, "Ali R.")
    assert "payout upcoming (next payout 2026-11-15)" in \
        _line(out.output, "Omar T.")


def test_report_no_schedule_timing(db, sample_project):
    # No schedule set, but billable invoices: the line keeps its
    # receivables shape and gains the matching timing phrase.
    fid = db.add_freelancer("No Sched", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    _split_freelancer(db, sample_project)  # weekly Wed, due 2026-10-28
    out = _report("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "payout no schedule" in _line(out.output, "No Sched")
    assert "payout due (next payout 2026-10-28)" in \
        _line(out.output, "Ali R.")


def test_report_per_currency_timing(db, sample_project):
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
    out = _report("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    usd_line = _line(out.output, "$100.00")
    eur_line = _line(out.output, "200.00 EUR")
    assert "Multi C." in usd_line and "Multi C." in eur_line
    assert "payout overdue (missed 2026-10-15)" in usd_line
    assert "payout upcoming (next payout 2026-11-15)" in eur_line


def test_report_unassigned_no_timing(db, sample_project):
    # An open invoice with no freelancer: the "unassigned" line
    # prints its receivables and no timing phrase.
    _split_freelancer(db, sample_project)  # weekly Wed, due 2026-10-28
    _upcoming_freelancer(db, sample_project)
    _invoice(db, sample_project, 50_000, "sent")
    out = _report("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    unassigned = _line(out.output, "unassigned")
    assert "payout" not in unassigned


def test_report_timing_as_of_projection(db, sample_project):
    # Same book, two report dates: due on the pay-run date, overdue
    # after it passes — the timing is projected, not today's.
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)
    _upcoming_freelancer(db, sample_project)
    due = _report("--as-of", "2026-10-15")
    assert due.exit_code == 0, due.output
    assert "payout due (next payout 2026-10-15)" in \
        _line(due.output, "Ali R.")
    overdue = _report("--as-of", "2026-10-31")
    assert overdue.exit_code == 0, overdue.output
    assert "payout overdue (missed 2026-10-15)" in \
        _line(overdue.output, "Ali R.")


def test_report_timing_invalid_as_of(db, sample_project):
    _split_freelancer(db, sample_project)
    _upcoming_freelancer(db, sample_project)
    out = _report("--as-of", "not-a-date")
    assert out.exit_code != 0
    assert "Traceback" not in out.output
