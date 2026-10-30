"""Agency-mode slice #11: settle the pay run.

Slice #10 listed the pay run (`reports.payout_due()` + `payout due`);
this slice records it: `payout settle-due` writes one payout per due
row, for the suggested amount, through the same over-payout guard as
`payout record`. Unlike `payout settle`, which settles the whole
book, this settles only the freelancers whose payout date has
arrived — a freelancer due next week keeps their suggestion until
their own pay run. No-split due freelancers are skipped, never
guessed at, and a second settle-due on the same pay run is a no-op.
"""

from __future__ import annotations

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import payout_due


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _due_freelancer(db, project, name="Ali R.", split_pct=70.0,
                    paid_cents=90_000, payout_cents=25_000,
                    cadence="weekly", day=3, currency="USD"):
    """Freelancer with one attributed invoice, `paid_cents` collected,
    `payout_cents` already paid out, and a payout schedule."""
    fid = db.add_freelancer(name, split_pct=split_pct,
                            payout_cadence=cadence, payout_day=day)
    iid = _invoice(db, project, 100_000, "sent", fid, currency=currency)
    db.record_payment(iid, paid_cents, method="bank")
    if payout_cents:
        db.record_payout(fid, payout_cents, currency)
    return fid


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_settle_due_records_only_due_freelancers(db, sample_project):
    # Weekly Wed: due 2026-10-28, 38k still due (63k share - 25k paid).
    due = _due_freelancer(db, sample_project)
    # Weekly Fri: due 2026-10-30, not on this pay run.
    later = _due_freelancer(db, sample_project, name="Zed Q.",
                            cadence="weekly", day=5)
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "380.00" in out.output
    assert db.paid_out_cents(due, "USD") == 63_000
    assert db.paid_out_cents(later, "USD") == 25_000  # untouched
    assert payout_due(db, as_of="2026-10-28") == []


def test_settle_due_within_days_covers_the_week(db, sample_project):
    later = _due_freelancer(db, sample_project, name="Zed Q.",
                            cadence="weekly", day=5)
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--as-of", "2026-10-28",
                                   "--within-days", "7"])
    assert out.exit_code == 0, out.output
    assert db.paid_out_cents(later, "USD") == 63_000


def test_settle_due_is_idempotent(db, sample_project):
    fid = _due_freelancer(db, sample_project)
    assert CliRunner().invoke(cli, ["payout", "settle-due",
                                    "--as-of", "2026-10-28"]).exit_code == 0
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "no payouts due" in out.output
    assert db.paid_out_cents(fid, "USD") == 63_000  # not double-paid


def test_settle_due_skips_no_split_freelancer(db, sample_project):
    split = _due_freelancer(db, sample_project)
    nosplit = _due_freelancer(db, sample_project, name="Sana K.",
                              split_pct=None)
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "settled 1 payout(s); skipped 1" in out.output
    assert db.paid_out_cents(split, "USD") == 63_000
    assert db.paid_out_cents(nosplit, "USD") == 25_000  # nothing guessed


def test_settle_due_dry_run_writes_nothing(db, sample_project):
    fid = _due_freelancer(db, sample_project)
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--as-of", "2026-10-28", "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "would settle" in out.output
    assert "nothing written" in out.output
    assert db.paid_out_cents(fid, "USD") == 25_000
    assert payout_due(db, as_of="2026-10-28") != []


def test_settle_due_filters_and_method(db, sample_project):
    ali = _due_freelancer(db, sample_project)  # USD, 380 due
    zed = _due_freelancer(db, sample_project, name="Zed Q.",
                          split_pct=50.0, paid_cents=40_000,
                          payout_cents=0, currency="EUR")  # EUR, 200 due
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--as-of", "2026-10-28",
                                   "--freelancer", str(zed)])
    assert out.exit_code == 0, out.output
    assert db.paid_out_cents(zed, "EUR") == 20_000
    assert db.paid_out_cents(ali, "USD") == 25_000
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--as-of", "2026-10-28",
                                   "--currency", "usd",
                                   "--method", "bank transfer"])
    assert out.exit_code == 0, out.output
    assert "bank transfer" in out.output
    assert db.paid_out_cents(ali, "USD") == 63_000
    assert db.list_payouts(ali)[0]["method"] == "bank transfer"


def test_settle_due_errors_and_empty_states(db, sample_project):
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--freelancer", "999"])
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--within-days", "-1"])
    assert out.exit_code != 0
    assert "within_days must be >= 0" in out.output
    # Empty roster.
    out = CliRunner().invoke(cli, ["payout", "settle-due"])
    assert out.exit_code == 0, out.output
    assert "no freelancers on the roster" in out.output
    # Roster with nothing due (unscheduled freelancer).
    fid = db.add_freelancer("Sana K.", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    out = CliRunner().invoke(cli, ["payout", "settle-due",
                                   "--as-of", "2026-10-28"])
    assert out.exit_code == 0, out.output
    assert "no payouts due" in out.output
    assert db.paid_out_cents(fid, "USD") == 0
