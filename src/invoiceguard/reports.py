"""Receivables aging report + CSV export (read-only).

Answers "who owes me what, and how late?" without opening the
dashboard, and gets the numbers out as CSV for an accountant. An
*open* invoice is one in status sent/overdue/partially-paid with an
outstanding balance > 0 — the same definition of outstanding the
dashboard totals use (invoice amount minus the payment-ledger sum).
Drafts, paid, and void invoices are not receivables and are excluded.

Aging buckets are by whole days overdue as of the report date; an
invoice due today (or later) is Current. Accrued late fees reuse the
contract §3 calculator in late_fees.py on the current outstanding
balance, so the report and `invoice late-fees` agree — with the same
honest caveat: no day-by-day amortization of payments made
mid-accrual. This module never writes to the database.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from .db import DB
from .late_fees import late_fee_summary

# (key, label) in display order.
BUCKETS = (
    ("current", "Current (not yet due)"),
    ("1-30", "1-30 days overdue"),
    ("31-60", "31-60 days overdue"),
    ("61-90", "61-90 days overdue"),
    ("90+", "90+ days overdue"),
)
BUCKET_LABELS = dict(BUCKETS)


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def aging_bucket(days_overdue: int | None) -> str:
    """Bucket key for a days-overdue count (None = no due date)."""
    if days_overdue is None or days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "90+"


def receivables_report(db: DB, as_of: str | date | None = None) -> dict:
    """Build the aging report over open invoices.

    Returns a dict with:
        as_of     — report date (ISO)
        rows      — one dict per open invoice (invoice fields plus
                    days_overdue, bucket, late_fees_cents,
                    total_due_cents), oldest debt first
        buckets   — {currency: [{key, label, count, outstanding_cents,
                    fees_cents, total_cents} for every bucket]}
        by_client — [{client_name, currency, count, outstanding_cents,
                    fees_cents, total_cents}] sorted by total due desc
        totals    — {currency: {count, outstanding_cents, fees_cents,
                    total_cents}}
    Aggregates are per currency — cents of different currencies are
    never added together.
    """
    today = _as_date(as_of) if as_of else date.today()
    rows = []
    for inv in db.open_invoices():
        days = None
        if inv.get("due_date"):
            days = max(0, (today - _as_date(inv["due_date"])).days)
        late = late_fee_summary(inv, as_of=today)
        row = dict(inv)
        row["days_overdue"] = days
        row["bucket"] = aging_bucket(days)
        row["late_fees_cents"] = late["fees_cents"]
        row["total_due_cents"] = (inv["outstanding_cents"]
                                  + late["fees_cents"])
        rows.append(row)
    # Oldest debt first; undated invoices last.
    rows.sort(key=lambda r: (-(r["days_overdue"] or 0), r["id"]))

    def _blank() -> dict:
        return {"count": 0, "outstanding_cents": 0,
                "fees_cents": 0, "total_cents": 0}

    buckets: dict[str, list[dict]] = {}
    totals: dict[str, dict] = {}
    clients: dict[tuple[str, str], dict] = {}
    for row in rows:
        cur = row["currency"]
        if cur not in buckets:
            buckets[cur] = [dict({"key": k, "label": lbl}, **_blank())
                            for k, lbl in BUCKETS]
            totals[cur] = _blank()
        b = next(x for x in buckets[cur] if x["key"] == row["bucket"])
        for agg in (b, totals[cur],
                    clients.setdefault((row["client_name"], cur),
                                       dict({"client_name": row["client_name"],
                                             "currency": cur}, **_blank()))):
            agg["count"] += 1
            agg["outstanding_cents"] += row["outstanding_cents"]
            agg["fees_cents"] += row["late_fees_cents"]
            agg["total_cents"] += row["total_due_cents"]
    by_client = sorted(clients.values(),
                       key=lambda c: (-c["total_cents"], c["client_name"]))
    return {
        "as_of": today.isoformat(),
        "rows": rows,
        "buckets": buckets,
        "by_client": by_client,
        "totals": totals,
    }


def _units(cents: int) -> str:
    """Cents as a plain decimal string for spreadsheets (1000.00)."""
    return f"{cents / 100:.2f}"


INVOICE_CSV_COLUMNS = [
    "invoice_id", "client", "project", "kind", "status", "currency",
    "amount", "paid", "outstanding", "late_fees", "total_due",
    "due_date", "days_overdue",
]


def invoices_csv(rows: list[dict]) -> str:
    """One CSV row per open invoice (the receivables_report rows)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(INVOICE_CSV_COLUMNS)
    for r in rows:
        w.writerow([
            r["id"], r["client_name"], r["project_title"], r["kind"],
            r["status"], r["currency"],
            _units(r["amount_cents"]), _units(r["paid_cents"]),
            _units(r["outstanding_cents"]), _units(r["late_fees_cents"]),
            _units(r["total_due_cents"]),
            r["due_date"] or "",
            "" if r["days_overdue"] is None else r["days_overdue"],
        ])
    return buf.getvalue()


PAYMENT_CSV_COLUMNS = [
    "payment_id", "invoice_id", "client", "project", "paid_at",
    "method", "amount", "currency", "note",
]


def payments_csv(db: DB) -> str:
    """The full payment ledger as CSV — every recorded payment,
    including payments against invoices that are now paid in full,
    so the export reconciles against bank statements."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(PAYMENT_CSV_COLUMNS)
    for p in db.all_payments():
        w.writerow([
            p["id"], p["invoice_id"], p["client_name"],
            p["project_title"], p["paid_at"], p["method"],
            _units(p["amount_cents"]), p["currency"], p["note"] or "",
        ])
    return buf.getvalue()
