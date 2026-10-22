"""Late-fee accrual calculator.

Implements contract §3 ("{late_fee_pct}% per month on the overdue balance
after {grace_days} days, compounding monthly") as an actual number: given
an invoice's outstanding balance, due date, and the project's late-fee
terms, compute the fees accrued as of a date.

Formula (this module is the single source of truth; the CLI, the
dashboard, and the dunning templates all use it):
  - fees start accruing the day after the grace period ends:
        accrual_start = due_date + grace_days + 1 day
  - one monthly fee is charged per *full monthly period elapsed*
    since accrual_start; period boundaries are anchored on the
    accrual-start day of month (clamped to the target month's last
    day), NOT on calendar-month boundaries — so a balance that starts
    accruing on Jan 31 has completed exactly one month on Feb 28 and
    can never be charged two months of fees one day later, and twelve
    months of accrual always charge exactly twelve fees. Fees
    compound on the running balance (outstanding balance + fees
    accrued so far), each month's fee rounded half-up to the cent
  - fees only ever add to what's owed; they never reduce the balance

History: before 0.8.4 the calculator counted *started calendar
months*, which charged a second month's fee the day after a month-end
accrual start (e.g. start Jan 31 -> 2 months on Feb 1) — more than
the contract's "% per month" wording promises. The elapsed-months
formula above is the fix.

Honest scope: this is a calculator, not an accounting standard. No
fee is charged for a monthly period that has not fully elapsed. The
calculator uses the *current* outstanding balance; it does not
retroactively amortize fees day-by-day for payments made mid-accrual
(re-run it after recording a payment to see the reduced run-rate).
"""

from __future__ import annotations

from calendar import monthrange
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


def _add_months(d: date, months: int) -> date:
    """`d` shifted by `months` calendar months, day clamped to the
    target month's last day (Jan 31 + 1 month -> Feb 28/29)."""
    total = (d.year * 12 + (d.month - 1)) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def months_accrued(start: str | date, as_of: str | date) -> int:
    """Number of *full* monthly periods elapsed from `start` to `as_of`.

    Period boundaries are anchored on the start day: start Jan 17 ->
    one month completes on Feb 17, two on Mar 17. start Jan 31 -> one
    month completes on Feb 28 (clamped). start == as_of counts as 0
    (no full month has elapsed yet); as_of before start counts as 0.
    """
    start, as_of = _as_date(start), _as_date(as_of)
    if as_of < start:
        return 0
    n = (as_of.year - start.year) * 12 + (as_of.month - start.month)
    if _add_months(start, n) > as_of:
        n -= 1
    return max(n, 0)


def accrue_late_fees(outstanding_cents: int, late_fee_pct: float,
                     grace_days: int, due_date: str | date | None,
                     as_of: str | date | None = None) -> dict:
    """Compute accrued late fees for an outstanding balance.

    Returns a dict with:
        fees_cents    — accrued late fees (0 when nothing accrues)
        months        — full monthly periods elapsed/charged
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
        if today >= start:
            # Accrual has begun but no full monthly period has
            # elapsed yet: no fee charged, but still report when
            # accrual started (the CLI/dashboard display it).
            zero["accrual_start"] = start.isoformat()
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
