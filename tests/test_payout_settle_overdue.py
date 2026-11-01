"""Agency-mode slice #13: settle the missed pay runs.

Slice #12 listed the missed runs (`reports.payout_overdue()` +
`payout overdue`); this slice records them: `payout settle-overdue`
writes one payout per overdue row, for the suggested amount, through
the same over-payout guard as `payout record`. Unlike
`payout settle-due`, which settles only the pay run whose date has
arrived, this settles only the runs already missed — a freelancer due
on the pay-run date itself stays in `payout settle-due`, and a
freelancer paid at or after the missed run is not overdue at all.
No-split overdue freelancers are skipped, never guessed at, and a
second settle-overdue on the same missed runs is a no-op.
"""

from __future__ import annotations

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import payout_overdue


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _overdue_freelancer(db, project, name="Ali R.", split_pct=70.0,
                        paid_cents=90_000, payout_cents=25_000,
                        cadence="weekly", day=5, currency="USD"):
    """Freelancer with one attributed invoice, `paid_cents` collected,
    `payout_cents` already paid out before the missed run, and a
    payout schedule. A prior payout must predate the missed run, or
    `payout_overdue` correctly excludes the row as already paid."""
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid, currency=currency)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, currency,
                         paid_at="2026-10-20")
    return fid


# 2026-10-28 is a Wednesday. Weekly Friday: next run Oct 30, missed
# Oct 23 — overdue as of Oct 28. Weekly Wednesday: due Oct 28 itself,
# so in `payout due`, never overdue.

def test_settle_overdue_records_only_overdue_freelancers(db,
                                                         sample_project):
    overdue = _overdue_freelancer(db, sample_project)
    # Weekly Wed: due 2026-10-28 itself, not a missed run.
    due_today = _overdue_freelancer(db, sample_project, name="Zed Q.",
                                    cadence="weekly", day=3)
    out = CliRunner().invoke(cli, ["payout", "settle-overdue",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "380.00" in out.output
    assert db.paid_out_cents(overdue, "USD") == 63_000
    assert db.paid_out_cents(due_today, "USD") == 25_000  # untouched
    assert payout_overdue(db, as_of="2026-10-28") == []


def test_settle_overdue_is_idempotent(db, sample_project):
    fid = _overdue_freelancer(db, sample_project)
    assert CliRunner().invoke(cli, ["payout", "settle-overdue",
                                    "--as-of", "2026-10-28"]).exit_code == 0
    out = CliRunner().invoke(cli, ["payout", "settle-overdue",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "no overdue payouts" in out.output
    assert db.paid_out_cents(fid, "USD") == 63_000  # not double-paid


def test_settle_overdue_skips_no_split_freelancer(db, sample_project):
    split = _overdue_freelancer(db, sample_project)
    nosplit = _overdue_freelancer(db, sample_project, name="Sana K.",
                                  split_pct=None)
    out = CliRunner().invoke(cli, ["payout", "settle-overdue",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "settled 1 payout(s); skipped 1" in out.output
    assert db.paid_out_cents(split, "USD") == 63_000
    assert db.paid_out_cents(nosplit, "USD") == 25_000  # nothing guessed


def test_settle_overdue_dry_run_writes_nothing(db, sample_project):
    fid = _overdue_freelancer(db, sample_project)
    out = CliRunner().invoke(cli, ["payout", "settle-overdue",
                                   "--as-of", "2026-10-28", "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "would settle" in out.output
    assert "missed 2026-10-23" in out.output
    assert "nothing written" in out.output
    assert db.paid_out_cents(fid, "USD") == 25_000
    assert payout_overdue(db, as_of="2026-10-28") != []


def test_settle_overdue_filters_and_method(db, sample_project):
    ali = _overdue_freelancer(db, sample_project)  # USD, 380 due
    zed = _overdue_freelancer(db, sample_project, name="Zed Q.",
                              split_pct=50.0, paid_cents=40_000,
                              payout_cents=0, currency="EUR")  # EUR, 200
    out = CliRunner().invoke(cli, ["payout", "settle-overdue",
                                   "--as-of", "2026-10-28",
                                   "--freelancer", str(zed)])
    assert out.exit_code == 0, out.output
    assert db.paid_out_cents(zed, "EUR") == 20_000
    assert db.paid_out_cents(ali, "USD") == 25_000
    out = CliRunner().invoke(cli, ["payout", "settle-overdue",
                                   "--as-of", "2026-10-28",
                                   "--currency", "usd",
                                   "--method", "bank transfer"])
    assert out.exit_code == 0, out.output
    assert "bank transfer" in out.output
    assert db.paid_out_cents(ali, "USD") == 63_000


def test_settle_overdue_unknown_freelancer(db, sample_project):
    out = CliRunner().invoke(cli, ["payout", "settle-overdue",
                                   "--freelancer", "999"])
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output


def test_settle_overdue_empty_roster(db, sample_project):
    out = CliRunner().invoke(cli, ["payout", "settle-overdue",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "no freelancers on the roster" in out.output
