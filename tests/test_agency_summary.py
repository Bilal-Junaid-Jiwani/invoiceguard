"""Agency-mode slice #8: whole-book agency summary.

`payout suggest` lists only actionable rows and `freelancer show`
covers one freelancer at a time; `reports.agency_summary()` + `agency
summary` give the whole book at a glance — one row per freelancer per
currency (billed / collected / outstanding / paid out / still owed,
plus the suggested payout where a split share is still due), with
per-currency totals across the roster. Read-only.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from invoiceguard.cli import cli
from invoiceguard.reports import agency_summary, agency_summary_totals


def _invoice(db, project, cents, status, freelancer_id=None,
             currency="USD"):
    iid = db.add_invoice(project["id"], "final", cents, currency,
                         status=status, due_date="2026-01-01")
    if freelancer_id is not None:
        db.assign_invoice_freelancer(iid, freelancer_id)
    return iid


def _book(db, project):
    """Two freelancers: Ali (70% split, USD) and Sana (no split, EUR).

    Ali: billed 100_000, collected 90_000, paid out 25_000 ->
    still owed 65_000, suggested 38_000 (70% of 90_000 minus 25_000).
    Sana: billed 50_000, collected 50_000, paid out 0 ->
    still owed 50_000, no suggestion (no split set).
    """
    ali = db.add_freelancer("Ali R.", split_pct=70.0)
    iid = _invoice(db, project, 100_000, "sent", ali)
    db.record_payment(iid, 90_000, method="bank")
    db.record_payout(ali, 25_000, "USD")
    sana = db.add_freelancer("Sana K.")
    iid = _invoice(db, project, 50_000, "sent", sana, currency="EUR")
    db.record_payment(iid, 50_000, method="bank")
    return ali, sana


def test_summary_rows_cover_whole_book(db, sample_project):
    ali, sana = _book(db, sample_project)
    rows = {(r["freelancer_id"], r["currency"]): r
            for r in agency_summary(db)}
    ali_row = rows[(ali, "USD")]
    assert (ali_row["invoiced_cents"], ali_row["collected_cents"],
            ali_row["outstanding_cents"], ali_row["paid_out_cents"],
            ali_row["still_owed_cents"]) == (100_000, 90_000, 10_000,
                                             25_000, 65_000)
    assert ali_row["suggested_cents"] == 38_000
    sana_row = rows[(sana, "EUR")]
    assert (sana_row["invoiced_cents"], sana_row["collected_cents"],
            sana_row["still_owed_cents"]) == (50_000, 50_000, 50_000)
    assert sana_row["suggested_cents"] is None  # no split: no suggestion


def test_summary_includes_freelancer_with_no_invoices(db):
    fid = db.add_freelancer("No Work Yet")
    rows = agency_summary(db)
    assert len(rows) == 1
    assert rows[0]["freelancer_id"] == fid
    assert rows[0]["currency"] is None
    assert rows[0]["invoiced_cents"] == 0
    assert rows[0]["suggested_cents"] is None


def test_summary_excludes_drafts_voids_and_unattributed(db, sample_project):
    fid = db.add_freelancer("Ali R.", split_pct=70.0)
    _invoice(db, sample_project, 100_000, "sent", fid)
    _invoice(db, sample_project, 999_000, "draft", fid)
    _invoice(db, sample_project, 888_000, "void", fid)
    _invoice(db, sample_project, 777_000, "sent")  # unattributed
    rows = agency_summary(db)
    assert len(rows) == 1
    assert rows[0]["invoiced_cents"] == 100_000


def test_summary_unknown_freelancer_raises(db):
    with pytest.raises(ValueError, match="no freelancer #999"):
        agency_summary(db, 999)


def test_cli_summary_prints_sections_and_totals(db, sample_project):
    _book(db, sample_project)
    out = CliRunner().invoke(cli, ["agency", "summary"])
    assert out.exit_code == 0, out.output
    assert "Ali R." in out.output
    assert "split: 70%" in out.output
    assert "Sana K." in out.output
    assert "no split set" in out.output
    assert "380.00" in out.output  # Ali's suggestion
    assert "TOTALS" in out.output
    assert "USD" in out.output and "EUR" in out.output
    # USD totals: billed 1,000.00, collected 900.00, still owed 650.00
    usd_line = [ln for ln in out.output.splitlines()
                if ln.strip().startswith("USD") and "TOTALS" not in ln]
    assert any("1,000.00" in ln and "900.00" in ln and "650.00" in ln
               for ln in usd_line)


def test_cli_summary_empty_roster(db):
    out = CliRunner().invoke(cli, ["agency", "summary"])
    assert out.exit_code == 0, out.output
    assert "no freelancers on the roster" in out.output


def test_cli_summary_freelancer_filter_and_unknown(db, sample_project):
    ali, _sana = _book(db, sample_project)
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--freelancer", str(ali)])
    assert out.exit_code == 0, out.output
    assert "Ali R." in out.output
    assert "Sana K." not in out.output
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--freelancer", "999"])
    assert out.exit_code != 0
    assert "no freelancer #999" in out.output


def test_cli_summary_is_read_only(db, sample_project):
    _book(db, sample_project)
    before = db.paid_out_cents(1, "USD")
    out = CliRunner().invoke(cli, ["agency", "summary"])
    assert out.exit_code == 0, out.output
    assert db.paid_out_cents(1, "USD") == before


def _timed_book(db, project):
    """Ali: 70% split, monthly day-15 schedule, USD suggestion live.

    Billed 100_000, collected 90_000, paid out 0 -> suggested 63_000.
    Sana: no split, no schedule, EUR available 50_000.
    """
    ali = db.add_freelancer("Ali R.", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, project, 100_000, "sent", ali)
    db.record_payment(iid, 90_000, method="bank")
    sana = db.add_freelancer("Sana K.")
    iid = _invoice(db, project, 50_000, "sent", sana, currency="EUR")
    db.record_payment(iid, 50_000, method="bank")
    return ali, sana


def test_summary_timing_due_on_pay_day(db, sample_project):
    ali, _sana = _timed_book(db, sample_project)
    rows = {(r["freelancer_id"], r["currency"]): r
            for r in agency_summary(db, as_of="2026-11-15")}
    ali_row = rows[(ali, "USD")]
    assert ali_row["payout_cadence"] == "monthly"
    assert ali_row["payout_day"] == 15
    assert ali_row["next_payout_date"] == "2026-11-15"
    assert ali_row["payout_status"] == "due"
    assert ali_row["missed_payout_date"] is None


def test_summary_timing_overdue_after_missed_run(db, sample_project):
    ali, _sana = _timed_book(db, sample_project)
    rows = {(r["freelancer_id"], r["currency"]): r
            for r in agency_summary(db, as_of="2026-11-20")}
    ali_row = rows[(ali, "USD")]
    assert ali_row["next_payout_date"] == "2026-12-15"
    assert ali_row["payout_status"] == "overdue"
    assert ali_row["missed_payout_date"] == "2026-11-15"


def test_summary_timing_upcoming_no_schedule_settled(db, sample_project):
    ali, sana = _timed_book(db, sample_project)
    # Ali is overdue at 2026-11-10 (the 2026-10-15 run was missed), so
    # "upcoming" needs a freelancer paid since the missed run: Omar,
    # monthly day 15, partial payout dated after 2026-10-15, share
    # still due, next run 2026-11-15 still ahead.
    omar = db.add_freelancer("Omar T.", split_pct=50.0,
                             payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, sample_project, 100_000, "sent", omar)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(omar, 10_000, "USD", paid_at="2026-10-20")
    rows = {(r["freelancer_id"], r["currency"]): r
            for r in agency_summary(db, as_of="2026-11-10")}
    assert rows[(omar, "USD")]["payout_status"] == "upcoming"
    assert rows[(omar, "USD")]["next_payout_date"] == "2026-11-15"
    sana_row = rows[(sana, "EUR")]
    assert sana_row["payout_status"] == "no schedule"
    assert sana_row["next_payout_date"] is None
    # Settling Ali's full share leaves a scheduled row with nothing due.
    db.record_payout(ali, 63_000, "USD")
    rows = {(r["freelancer_id"], r["currency"]): r
            for r in agency_summary(db, as_of="2026-11-10")}
    assert rows[(ali, "USD")]["payout_status"] == "settled"


def test_summary_timing_no_billable_row_carries_schedule(db):
    fid = db.add_freelancer("No Work Yet", payout_cadence="weekly",
                            payout_day=5)
    rows = agency_summary(db, as_of="2026-11-10")
    assert len(rows) == 1
    assert rows[0]["payout_cadence"] == "weekly"
    assert rows[0]["next_payout_date"] == "2026-11-13"
    assert rows[0]["payout_status"] is None  # no currency: no status


def test_cli_summary_as_of_prints_timing(db, sample_project):
    _timed_book(db, sample_project)
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--as-of", "2026-11-15"])
    assert out.exit_code == 0, out.output
    assert "payout due" in out.output
    assert "next payout 2026-11-15" in out.output
    assert "payout no schedule" in out.output
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--as-of", "2026-11-20"])
    assert out.exit_code == 0, out.output
    assert "payout overdue" in out.output
    assert "missed 2026-11-15" in out.output


def test_cli_summary_bad_as_of_is_clean_error(db, sample_project):
    _timed_book(db, sample_project)
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--as-of", "not-a-date"])
    assert out.exit_code != 0
    assert "Traceback" not in out.output


# --- Agency-mode slice #15: payout timing on the TOTALS ----------------
# Slice #14 put timing on every per-freelancer currency line but left
# the TOTALS lines answering only "how much". The rollup reports the
# most urgent status per currency, derived from the rows' own statuses
# (no new rules): overdue beats due beats upcoming; when nothing is
# actionable the shared status wins, and a settled/no-schedule mix
# carries no status rather than a misleading one.

def _totals_book(db, project):
    """Four USD freelancers, all with a live suggestion at 2026-11-20.

    Ali (monthly 15, 70%): overdue, missed 2026-11-15. Zoe (monthly
    10, 50%): overdue, missed 2026-11-10. Omar (monthly 20, 50%):
    due on 2026-11-20 itself. Quinn (monthly 25, 50%): paid 10_000 on
    2026-10-30, after the missed 2026-10-25 run, share still due ->
    upcoming, next 2026-11-25.
    """
    ali = db.add_freelancer("Ali R.", split_pct=70.0,
                            payout_cadence="monthly", payout_day=15)
    iid = _invoice(db, project, 100_000, "sent", ali)
    db.record_payment(iid, 90_000, method="bank")
    zoe = db.add_freelancer("Zoe M.", split_pct=50.0,
                            payout_cadence="monthly", payout_day=10)
    iid = _invoice(db, project, 100_000, "sent", zoe)
    db.record_payment(iid, 80_000, method="bank")
    omar = db.add_freelancer("Omar T.", split_pct=50.0,
                             payout_cadence="monthly", payout_day=20)
    iid = _invoice(db, project, 100_000, "sent", omar)
    db.record_payment(iid, 80_000, method="bank")
    quinn = db.add_freelancer("Quinn P.", split_pct=50.0,
                              payout_cadence="monthly", payout_day=25)
    iid = _invoice(db, project, 100_000, "sent", quinn)
    db.record_payment(iid, 80_000, method="bank")
    db.record_payout(quinn, 10_000, "USD", paid_at="2026-10-30")
    return ali, zoe, omar, quinn


def test_totals_money_and_overdue_wins(db, sample_project):
    _totals_book(db, sample_project)
    totals = agency_summary_totals(db, as_of="2026-11-20")
    usd = totals["USD"]
    assert (usd["invoiced_cents"], usd["collected_cents"],
            usd["outstanding_cents"], usd["paid_out_cents"],
            usd["still_owed_cents"]) == (400_000, 330_000, 70_000,
                                         10_000, 320_000)
    # Suggested: Ali 63_000 + Zoe 40_000 + Omar 40_000 + Quinn 30_000.
    assert usd["suggested_cents"] == 173_000
    assert usd["has_suggested"] is True
    assert usd["payout_status"] == "overdue"
    assert usd["payout_freelancers"] == 2  # Ali + Zoe, not Omar/Quinn
    assert usd["missed_payout_date"] == "2026-11-10"  # earliest missed
    assert usd["next_payout_date"] is None


def test_totals_due_beats_upcoming(db, sample_project):
    _ali, _zoe, omar, quinn = _totals_book(db, sample_project)
    totals = agency_summary_totals(db, as_of="2026-11-20")
    # Restrict to the due + upcoming freelancers only.
    totals = agency_summary_totals(db, freelancer_id=omar,
                                   as_of="2026-11-20")
    assert totals["USD"]["payout_status"] == "due"
    assert totals["USD"]["payout_freelancers"] == 1
    assert totals["USD"]["next_payout_date"] == "2026-11-20"
    totals = agency_summary_totals(db, freelancer_id=quinn,
                                   as_of="2026-11-20")
    assert totals["USD"]["payout_status"] == "upcoming"
    assert totals["USD"]["next_payout_date"] == "2026-11-25"


def test_totals_settled_no_schedule_and_mixed(db, sample_project):
    ali, sana = _timed_book(db, sample_project)
    # Sana (EUR, no schedule) on her own: shared no-schedule status.
    totals = agency_summary_totals(db, freelancer_id=sana,
                                   as_of="2026-11-10")
    assert totals["EUR"]["payout_status"] == "no schedule"
    # Ali settled (full 63_000 share paid) on his own: settled.
    db.record_payout(ali, 63_000, "USD")
    totals = agency_summary_totals(db, freelancer_id=ali,
                                   as_of="2026-11-10")
    assert totals["USD"]["payout_status"] == "settled"
    # Mixed settled (Ali, USD) + no-schedule (Sana, USD invoice):
    # no single status describes the currency.
    iid = _invoice(db, sample_project, 50_000, "sent", sana)
    db.record_payment(iid, 50_000, method="bank")
    totals = agency_summary_totals(db, as_of="2026-11-10")
    assert totals["USD"]["payout_status"] is None
    assert totals["USD"]["payout_freelancers"] is None


def test_totals_excludes_no_billable_freelancers(db):
    db.add_freelancer("No Work Yet")
    assert agency_summary_totals(db) == {}


def test_totals_unknown_freelancer_raises(db):
    with pytest.raises(ValueError, match="no freelancer #999"):
        agency_summary_totals(db, 999)


def _totals_lines(output):
    lines = output.splitlines()
    start = lines.index("TOTALS")
    return [ln for ln in lines[start + 1:] if ln.strip()]


def test_cli_totals_line_carries_rollup(db, sample_project):
    _totals_book(db, sample_project)
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--as-of", "2026-11-20"])
    assert out.exit_code == 0, out.output
    usd = [ln for ln in _totals_lines(out.output)
           if ln.strip().startswith("USD")]
    assert len(usd) == 1
    assert "payout overdue (2 freelancers, missed 2026-11-10)" in usd[0]
    # Money totals unchanged by the rollup.
    assert "4,000.00" in usd[0] and "3,300.00" in usd[0]


def test_cli_totals_mixed_currency_carries_no_timing(db, sample_project):
    ali, sana = _timed_book(db, sample_project)
    db.record_payout(ali, 63_000, "USD")
    iid = _invoice(db, sample_project, 50_000, "sent", sana)
    db.record_payment(iid, 50_000, method="bank")
    out = CliRunner().invoke(cli, ["agency", "summary",
                                   "--as-of", "2026-11-10"])
    assert out.exit_code == 0, out.output
    usd = [ln for ln in _totals_lines(out.output)
           if ln.strip().startswith("USD")]
    assert len(usd) == 1
    assert "payout" not in usd[0]
