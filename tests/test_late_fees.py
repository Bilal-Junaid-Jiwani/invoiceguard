"""Late-fee accrual calculator tests: formula, edge cases, invoice wiring."""

from __future__ import annotations

from invoiceguard.late_fees import (accrual_start_date, accrue_late_fees,
                                    late_fee_summary, months_accrued)


class TestAccrualStart:
    def test_grace_plus_one(self):
        assert str(accrual_start_date("2026-01-01", 15)) == "2026-01-17"

    def test_zero_grace(self):
        assert str(accrual_start_date("2026-01-01", 0)) == "2026-01-02"


class TestMonthsAccrued:
    """0.8.4 semantics: FULL monthly periods elapsed, anchored on the
    accrual-start day — not started calendar months."""

    def test_same_day_counts_zero(self):
        assert months_accrued("2026-01-17", "2026-01-17") == 0

    def test_partial_month_counts_zero(self):
        assert months_accrued("2026-01-17", "2026-01-18") == 0
        assert months_accrued("2026-01-17", "2026-02-16") == 0

    def test_first_full_month(self):
        assert months_accrued("2026-01-17", "2026-02-17") == 1
        assert months_accrued("2026-01-17", "2026-03-17") == 2

    def test_calendar_boundary_alone_adds_nothing(self):
        # Crossing into a new calendar month is not a month of accrual.
        assert months_accrued("2026-01-17", "2026-02-01") == 0

    def test_year_boundary(self):
        assert months_accrued("2025-12-20", "2026-02-05") == 1
        assert months_accrued("2025-12-20", "2026-02-20") == 2

    def test_month_end_start_never_doubles(self):
        # The review regression: accrual starting Jan 31 charged TWO
        # months on Feb 1 under calendar-month counting (2.02x in a
        # day). Elapsed counting: 0 on Feb 1, 1 on Feb 28 (clamped),
        # 2 on Mar 31, exactly 12 after a full year.
        assert months_accrued("2026-01-31", "2026-02-01") == 0
        assert months_accrued("2026-01-31", "2026-02-27") == 0
        assert months_accrued("2026-01-31", "2026-02-28") == 1
        assert months_accrued("2026-01-31", "2026-03-31") == 2
        assert months_accrued("2026-01-31", "2027-01-31") == 12

    def test_before_start(self):
        assert months_accrued("2026-01-17", "2026-01-16") == 0


class TestAccrue:
    def test_no_due_date_accrues_nothing(self):
        r = accrue_late_fees(100_000, 1.5, 15, None, as_of="2027-01-01")
        assert r["fees_cents"] == 0 and r["months"] == 0

    def test_within_grace_accrues_nothing(self):
        # due 2026-01-01, grace 15 -> accrual starts 2026-01-17
        r = accrue_late_fees(100_000, 1.5, 15, "2026-01-01",
                             as_of="2026-01-16")
        assert r["fees_cents"] == 0 and r["months"] == 0

    def test_first_day_of_accrual_charges_nothing_yet(self):
        # Accrual starts 2026-01-17; no full month has elapsed that day.
        r = accrue_late_fees(100_000, 1.5, 15, "2026-01-01",
                             as_of="2026-01-17")
        assert r["months"] == 0
        assert r["fees_cents"] == 0
        assert r["total_cents"] == 100_000

    def test_first_full_month_charges_one_fee(self):
        # One full monthly period after the 2026-01-17 accrual start.
        r = accrue_late_fees(100_000, 1.5, 15, "2026-01-01",
                             as_of="2026-02-17")
        assert r["months"] == 1
        assert r["fees_cents"] == 1_500  # 1.5% of $1000
        assert r["total_cents"] == 101_500

    def test_month_end_accrual_never_doubles(self):
        # Review regression end-to-end: due Jan 16, grace 15 -> accrual
        # starts Jan 31; on Feb 1 the old calendar-month math charged
        # 2 months of fees for 1 day of accrual.
        r = accrue_late_fees(100_000, 1.5, 15, "2026-01-15",
                             as_of="2026-02-01")
        # accrual_start = due + grace + 1 = Jan 31; Feb 1 is 1 day in
        assert r["accrual_start"] == "2026-01-31"
        assert r["months"] == 0
        assert r["fees_cents"] == 0

    def test_compounds_monthly(self):
        # $1000 @ 1.5%/mo for 3 full months (start Jan 17 -> Apr 17),
        # per-month half-up rounding:
        # m1: 1500.00 -> bal 101500 ; m2: 1522.50 -> 1523, bal 103023
        # m3: 1545.345 -> 1545
        r = accrue_late_fees(100_000, 1.5, 15, "2026-01-01",
                             as_of="2026-04-17")
        assert r["months"] == 3
        assert r["fees_cents"] == 1_500 + 1_523 + 1_545
        assert r["total_cents"] == 100_000 + r["fees_cents"]

    def test_zero_rate_accrues_nothing(self):
        r = accrue_late_fees(100_000, 0.0, 15, "2026-01-01",
                             as_of="2027-01-01")
        assert r["fees_cents"] == 0

    def test_zero_outstanding_accrues_nothing(self):
        r = accrue_late_fees(0, 1.5, 15, "2026-01-01", as_of="2027-01-01")
        assert r["fees_cents"] == 0 and r["total_cents"] == 0

    def test_rate_used_reported(self):
        r = accrue_late_fees(100_000, 2.0, 15, "2026-01-01",
                             as_of="2026-02-17")
        assert r["rate_pct"] == 2.0
        assert r["fees_cents"] == 2_000

    def test_accrual_start_reported(self):
        r = accrue_late_fees(100_000, 1.5, 15, "2026-01-01",
                             as_of="2026-02-01")
        assert r["accrual_start"] == "2026-01-17"


class TestSummary:
    def test_summary_from_invoice_full(self, db, sample_project):
        iid = db.add_invoice(sample_project["id"], "final", 100_000, "USD",
                             status="sent", due_date="2026-01-01")
        full = db.invoice_full(iid)
        s = late_fee_summary(full, as_of="2026-02-17")
        assert s["months"] == 1
        assert s["fees_cents"] == 1_500
        assert s["total_cents"] == 101_500

    def test_summary_missing_keys_degrades_to_zero(self):
        s = late_fee_summary({}, as_of="2026-01-17")
        assert s["fees_cents"] == 0 and s["months"] == 0

    def test_summary_respects_payments(self, db, sample_project):
        iid = db.add_invoice(sample_project["id"], "final", 100_000, "USD",
                             status="sent", due_date="2026-01-01")
        db.record_payment(iid, 40_000)  # $600 outstanding
        full = db.invoice_full(iid)
        s = late_fee_summary(full, as_of="2026-02-17")
        assert s["fees_cents"] == 900  # 1.5% of $600
        assert s["total_cents"] == 60_000 + 900
