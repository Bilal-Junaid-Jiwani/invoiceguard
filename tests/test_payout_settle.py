"""Agency-mode slice #7: payout settlement.

Slice #6 answered "what do I pay next?" per freelancer per currency
(`reports.payout_suggestions()` + `payout suggest`); slice #7 records
that answer: `payout settle` writes one payout per suggestion, for the
suggested amount, through the same over-payout guard as `payout
record`. Freelancers with no split set have no share to settle, so they
are skipped, never guessed at — and a second settle is a no-op, because
a settled freelancer produces no further suggestion.
"""

from __future__ import annotations

from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import payout_suggestions


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _split_freelancer(db, project, split_pct=70.0, paid_cents=90_000,
                      currency="USD", name="Ali R."):
    """Freelancer with one attributed invoice and `paid_cents` collected."""
    fid = db.add_freelancer(name, split_pct=split_pct)
    iid = _invoice(db, project, 100_000, "sent", fid, currency=currency)
    db.record_payment(iid, paid_cents, method="bank")
    return fid


def test_settle_records_suggested_amount(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # 70% of 90k = 630
    db.record_payout(fid, 25_000, "USD")  # 380 still due
    out = CliRunner().invoke(cli, ["payout", "settle"])
    assert out.exit_code == 0, out.output
    assert "380.00" in out.output
    assert db.paid_out_cents(fid, "USD") == 63_000
    assert payout_suggestions(db, fid) == []  # settled: nothing left


def test_settle_is_idempotent(db, sample_project):
    fid = _split_freelancer(db, sample_project)
    assert CliRunner().invoke(cli, ["payout", "settle"]).exit_code == 0
    out = CliRunner().invoke(cli, ["payout", "settle"])
    assert out.exit_code == 0, out.output
    assert "no payouts to settle" in out.output
    assert db.paid_out_cents(fid, "USD") == 63_000  # not double-paid


def test_settle_skips_no_split_freelancer(db, sample_project):
    fid = db.add_freelancer("Sana K.")  # no split: available, no share
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    out = CliRunner().invoke(cli, ["payout", "settle"])
    assert out.exit_code == 0, out.output
    assert "no payouts to settle" in out.output
    assert "split" in out.output
    assert db.paid_out_cents(fid, "USD") == 0  # nothing guessed at


def test_settle_mixed_skips_only_the_no_split_one(db, sample_project):
    split_fid = _split_freelancer(db, sample_project)
    nosplit = db.add_freelancer("Sana K.")
    iid = _invoice(db, sample_project, 100_000, "sent", nosplit)
    db.record_payment(iid, 90_000, method="bank")
    out = CliRunner().invoke(cli, ["payout", "settle"])
    assert out.exit_code == 0, out.output
    assert "settled 1 payout(s); skipped 1" in out.output
    assert db.paid_out_cents(split_fid, "USD") == 63_000
    assert db.paid_out_cents(nosplit, "USD") == 0


def test_settle_dry_run_writes_nothing(db, sample_project):
    fid = _split_freelancer(db, sample_project)
    out = CliRunner().invoke(cli, ["payout", "settle", "--dry-run"])
    assert out.exit_code == 0, out.output
    assert "would settle" in out.output
    assert "nothing written" in out.output
    assert db.paid_out_cents(fid, "USD") == 0
    assert payout_suggestions(db, fid) != []


def test_settle_filters(db, sample_project):
    ali = _split_freelancer(db, sample_project)  # USD, 630 due
    zed = _split_freelancer(db, sample_project, split_pct=50.0,
                            paid_cents=40_000, currency="EUR",
                            name="Zed Q.")  # EUR, 200 due
    out = CliRunner().invoke(cli, ["payout", "settle",
                                   "--freelancer", str(zed)])
    assert out.exit_code == 0, out.output
    assert db.paid_out_cents(zed, "EUR") == 20_000
    assert db.paid_out_cents(ali, "USD") == 0
    out = CliRunner().invoke(cli, ["payout", "settle",
                                   "--currency", "usd"])
    assert out.exit_code == 0, out.output
    assert db.paid_out_cents(ali, "USD") == 63_000


def test_settle_unknown_freelancer_and_method(db, sample_project):
    out = CliRunner().invoke(cli, ["payout", "settle",
                                   "--freelancer", "999"])
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output
    fid = _split_freelancer(db, sample_project)
    out = CliRunner().invoke(cli, ["payout", "settle",
                                   "--method", "bank transfer"])
    assert out.exit_code == 0, out.output
    assert "bank transfer" in out.output
    assert db.list_payouts(fid)[0]["method"] == "bank transfer"
