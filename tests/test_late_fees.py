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
    def test_same_day_counts_one(self):
        assert months_accrued("2026-01-17", "2026-01-17") == 1

    def test_partial_month_counts_full(self):
        assert months_accrued("2026-01-17", "2026-01-18") == 1

    def test_month_boundary(self):
        assert months_accrued("2026-01-17", "2026-02-01") == 2

    def test_year_boundary(self):
        assert months_accrued("2025-12-20", "2026-02-05") == 3

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

    def test_first_day_of_accrual_is_one_month(self):
        r = accrue_late_fees(100_000, 1.5, 15, "2026-01-01",
                             as_of="2026-01-17")
        assert r["months"] == 1
        assert r["fees_cents"] == 1_500  # 1.5% of $1000
        assert r["total_cents"] == 101_500

    def test_compounds_monthly(self):
        # $1000 @ 1.5%/mo for 3 months, per-month half-up rounding:
        # m1: 1500.00 -> bal 101500 ; m2: 1522.50 -> 1523, bal 103023
        # m3: 1545.345 -> 1545
        r = accrue_late_fees(100_000, 1.5, 15, "2026-01-01",
                             as_of="2026-03-20")
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
                             as_of="2026-01-17")
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
        s = late_fee_summary(full, as_of="2026-01-17")
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
        s = late_fee_summary(full, as_of="2026-01-17")
        assert s["fees_cents"] == 900  # 1.5% of $600
        assert s["total_cents"] == 60_000 + 900
