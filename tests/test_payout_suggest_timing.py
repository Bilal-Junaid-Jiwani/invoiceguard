"""Agency-mode slice #17: payout timing on `payout suggest`.

`payout suggest` answers "what do I pay next" per freelancer per
currency, but its rows carried no timing — telling an overdue
suggestion from an upcoming one meant cross-checking `payout due`
and `payout overdue`. This slice appends the same timing to each
suggestion line, projected with `--as-of` (default today), derived
from the exact `payout due` / `overdue` rules — no new rules.
Read-only. A suggestion row is actionable by construction, so
`settled` never appears here (unlike the agency summary, which also
covers settled rows).
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import payout_suggestion_timing


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


def _suggest(*extra):
    return CliRunner().invoke(cli, ["payout", "suggest", *extra])


# 2026-10-28 is a Wednesday (ISO weekday 3).

def test_suggest_due_timing(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # weekly Wed
    out = _suggest("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "payout due (next payout 2026-10-28)" in out.output


def test_suggest_overdue_timing(db, sample_project):
    # Monthly day 15, as of 2026-10-31: missed Oct 15, next Nov 15.
    fid = _split_freelancer(db, sample_project, cadence="monthly",
                            day=15)
    out = _suggest("--as-of", "2026-10-31")
    assert out.exit_code == 0, out.output
    assert "payout overdue (missed 2026-10-15)" in out.output


def test_suggest_upcoming_timing(db, sample_project):
    # Monthly day 15, as of 2026-11-10: the Oct 15 run was missed, but a
    # payout dated after it (Oct 20) means the run is not still owed —
    # the share still due waits for the next run, Nov 15, still ahead.
    fid = db.add_freelancer("Omar T.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(fid, 10_000, "USD", paid_at="2026-10-20")
    out = _suggest("--as-of", "2026-11-10")
    assert out.exit_code == 0, out.output
    assert "payout upcoming (next payout 2026-11-15)" in out.output


def test_suggest_no_schedule_timing(db, sample_project):
    fid = db.add_freelancer("Sana K.", split_pct=70.0)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    out = _suggest("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "payout no schedule" in out.output


def test_suggest_no_split_row_carries_timing(db, sample_project):
    # No split set: the available row still gets its timing.
    fid = db.add_freelancer("Noor A.", payout_cadence="weekly",
                            payout_day=3)
    iid = _invoice(db, sample_project, 100_000, "sent", fid)
    db.record_payment(iid, 90_000, method="bank")
    out = _suggest("--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "available — no split set" in out.output
    assert "payout due (next payout 2026-10-28)" in out.output


def test_suggest_timing_respects_freelancer_filter(db, sample_project):
    due_fid = _split_freelancer(db, sample_project, name="Due D.")
    _split_freelancer(db, sample_project, name="Later L.",
                      cadence="monthly", day=15)
    out = _suggest("--freelancer", str(due_fid),
                   "--as-of", "2026-10-28")
    assert out.exit_code == 0, out.output
    assert "Due D." in out.output
    assert "Later L." not in out.output
    assert "payout due (next payout 2026-10-28)" in out.output


def test_suggest_invalid_as_of_is_clean_error(db, sample_project):
    _split_freelancer(db, sample_project)
    out = _suggest("--as-of", "not-a-date")
    assert out.exit_code != 0
    assert "Traceback" not in out.output


def test_suggest_timing_library(db, sample_project):
    fid = _split_freelancer(db, sample_project)  # weekly Wed
    timing = payout_suggestion_timing(db, as_of="2026-10-28")
    assert timing[(fid, "USD")] == {
        "payout_status": "due",
        "next_payout_date": "2026-10-28",
        "missed_payout_date": None,
    }


def test_suggest_timing_unknown_freelancer(db):
    with pytest.raises(ValueError, match="no freelancer #"):
        payout_suggestion_timing(db, freelancer_id=999,
                                 as_of="2026-10-28")
