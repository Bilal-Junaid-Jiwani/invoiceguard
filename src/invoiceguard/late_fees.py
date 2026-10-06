"""Late-fee accrual calculator.

Implements contract §3 ("{late_fee_pct}% per month on the overdue balance
after {grace_days} days, compounding monthly") as an actual number: given
an invoice's outstanding balance, due date, and the project's late-fee
terms, compute the fees accrued as of a date.

Formula (this module is the single source of truth; the CLI, the
dashboard, and the dunning templates all use it):
  - fees start accruing the day after the grace period ends:
        accrual_start = due_date + grace_days + 1 day
  - each started calendar month on/after accrual_start charges one full
    monthly fee; fees compound on the running balance (outstanding
    balance + fees accrued so far), each month's fee rounded half-up
    to the cent
  - fees only ever add to what's owed; they never reduce the balance

Honest scope: this is a calculator, not an accounting standard. A
partial month counts as a full month — matching the contract's
"compounding monthly" wording, the way most "% per month" penalty
clauses work. The calculator uses the *current* outstanding balance;
it does not retroactively amortize fees day-by-day for payments made
mid-accrual (re-run it after recording a payment to see the reduced
run-rate).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def accrual_start_date(due_date: str | date, grace_days: int) -> date:
    """First day late fees can accrue: due_date + grace_days + 1 day."""
    from datetime import timedelta

    return _as_date(due_date) + timedelta(days=int(grace_days) + 1)


def months_accrued(start: str | date, as_of: str | date) -> int:
    """Number of started calendar months from `start` through `as_of`.

    start == as_of counts as 1 (the month the start falls in).
    as_of before start counts as 0.
    """
    start, as_of = _as_date(start), _as_date(as_of)
    if as_of < start:
        return 0
    return (as_of.year - start.year) * 12 + (as_of.month - start.month) + 1


def accrue_late_fees(outstanding_cents: int, late_fee_pct: float,
                     grace_days: int, due_date: str | date | None,
                     as_of: str | date | None = None) -> dict:
    """Compute accrued late fees for an outstanding balance.

    Returns a dict with:
        fees_cents    — accrued late fees (0 when nothing accrues)
        months        — started months charged
        accrual_start — first accrual day (ISO date) or None
        rate_pct      — the monthly rate used
        total_cents   — outstanding + fees
    """
    today = _as_date(as_of) if as_of else date.today()
    zero = {
        "fees_cents": 0,
        "months": 0,
        "accrual_start": None,
        "rate_pct": float(late_fee_pct or 0.0),
        "total_cents": int(outstanding_cents or 0),
    }
    if not due_date or not outstanding_cents or outstanding_cents <= 0:
        return zero
    if not late_fee_pct or late_fee_pct <= 0:
        return zero
    start = accrual_start_date(due_date, grace_days)
    n = months_accrued(start, today)
    if n <= 0:
        return zero
    rate = Decimal(str(late_fee_pct)) / Decimal(100)
    balance = Decimal(outstanding_cents)
    fees = Decimal(0)
    for _ in range(n):
        month_fee = (balance * rate).quantize(Decimal("1"),
                                              rounding=ROUND_HALF_UP)
        fees += month_fee
        balance += month_fee
    return {
        "fees_cents": int(fees),
        "months": n,
        "accrual_start": start.isoformat(),
        "rate_pct": float(late_fee_pct),
        "total_cents": int(outstanding_cents) + int(fees),
    }


def late_fee_summary(invoice: dict,
                     as_of: str | date | None = None) -> dict:
    """Accrue late fees for an invoice dict.

    Accepts the dicts returned by DB.invoice_full() and the dashboard's
    get_invoice(): needs due_date, late_fee_pct, late_fee_grace_days,
    and outstanding_cents. Missing keys degrade to a zero-fee summary
    rather than raising.
    """
    return accrue_late_fees(
        outstanding_cents=int(invoice.get("outstanding_cents") or 0),
        late_fee_pct=float(invoice.get("late_fee_pct") or 0.0),
        grace_days=int(invoice.get("late_fee_grace_days") or 0),
        due_date=invoice.get("due_date"),
        as_of=as_of,
    )
