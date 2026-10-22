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


def receivables_report(db: DB, as_of: str | date | None = None,
                       freelancer_id: int | None = None) -> dict:
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
        by_freelancer — like by_client but per attributed freelancer
                    (unattributed invoices are grouped under
                    "unassigned")
        totals    — {currency: {count, outstanding_cents, fees_cents,
                    total_cents}}
    Aggregates are per currency — cents of different currencies are
    never added together.

    Pass freelancer_id to restrict the report to invoices attributed to
    one freelancer; rows for that filter still carry by_freelancer for
    the single filtered freelancer.
    """
    today = _as_date(as_of) if as_of else date.today()
    rows = []
    for inv in db.open_invoices():
        if (freelancer_id is not None
                and inv.get("freelancer_id") != freelancer_id):
            continue
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
    freelancers: dict[tuple[str, str], dict] = {}
    for row in rows:
        cur = row["currency"]
        if cur not in buckets:
            buckets[cur] = [dict({"key": k, "label": lbl}, **_blank())
                            for k, lbl in BUCKETS]
            totals[cur] = _blank()
        b = next(x for x in buckets[cur] if x["key"] == row["bucket"])
        f_name = row.get("freelancer_name") or "unassigned"
        f_agg = freelancers.setdefault(
            (f_name, cur), dict({"freelancer_name": f_name,
                                "currency": cur}, **_blank()))
        for agg in (b, totals[cur], f_agg,
                    clients.setdefault((row["client_name"], cur),
                                       dict({"client_name": row["client_name"],
                                             "currency": cur}, **_blank()))):
            agg["count"] += 1
            agg["outstanding_cents"] += row["outstanding_cents"]
            agg["fees_cents"] += row["late_fees_cents"]
            agg["total_cents"] += row["total_due_cents"]
    by_client = sorted(clients.values(),
                       key=lambda c: (-c["total_cents"], c["client_name"]))
    by_freelancer = sorted(
        freelancers.values(),
        key=lambda c: (-c["total_cents"], c["freelancer_name"]))
    return {
        "as_of": today.isoformat(),
        "rows": rows,
        "buckets": buckets,
        "by_client": by_client,
        "by_freelancer": by_freelancer,
        "totals": totals,
    }


def _units(cents: int) -> str:
    """Cents as a plain decimal string for spreadsheets (1000.00)."""
    return f"{cents / 100:.2f}"


# Characters that make spreadsheet apps treat a cell as a formula or
# a command instead of text — see the OWASP "CSV Injection" cheat
# sheet. Client names, project titles, payment methods, and notes are
# free text, so a cell that *starts* with one of these gets a leading
# single quote. Spreadsheet apps render the cell as plain text (the
# quote is a text marker, not displayed), so nothing about a benign
# value changes — only the formula interpretation is defused.
_CSV_FORMULA_LEAD = ("=", "+", "-", "@", "|", "%", "\t", "\r")


def _csv_cell(value):
    """Neutralize CSV formula injection on one cell value."""
    if isinstance(value, str) and value[:1] in _CSV_FORMULA_LEAD:
        return "'" + value
    return value


INVOICE_CSV_COLUMNS = [
    "invoice_id", "client", "project", "kind", "status", "currency",
    "amount", "paid", "outstanding", "late_fees", "total_due",
    "due_date", "days_overdue", "freelancer",
]


def invoices_csv(rows: list[dict]) -> str:
    """One CSV row per open invoice (the receivables_report rows).

    Free-text cells (client, project, freelancer) are neutralized
    against CSV formula injection (see `_csv_cell`).
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(INVOICE_CSV_COLUMNS)
    for r in rows:
        w.writerow([_csv_cell(v) for v in [
            r["id"], r["client_name"], r["project_title"], r["kind"],
            r["status"], r["currency"],
            _units(r["amount_cents"]), _units(r["paid_cents"]),
            _units(r["outstanding_cents"]), _units(r["late_fees_cents"]),
            _units(r["total_due_cents"]),
            r["due_date"] or "",
            "" if r["days_overdue"] is None else r["days_overdue"],
            r.get("freelancer_name") or "",
        ]])
    return buf.getvalue()


PAYMENT_CSV_COLUMNS = [
    "payment_id", "invoice_id", "client", "project", "paid_at",
    "method", "amount", "currency", "note",
]


def freelancer_earnings(db: DB, freelancer_id: int | None = None) -> list[dict]:
    """Per-freelancer billed / collected / outstanding, per currency.

    *Billed* is the sum of amount_cents over the freelancer's attributed
    invoices in a real state — drafts (not yet bills) and voids (cancelled)
    are excluded. *Collected* is the payment-ledger sum recorded against
    those invoices; *outstanding* is billed minus collected. Currencies
    are never mixed: a freelancer with invoices in two currencies gets
    one row per currency, sorted with the largest collected total first.

    Every roster freelancer appears, even with no billable invoices (a
    single row with currency None and zero amounts). Unattributed
    invoices belong to nobody and are not counted anywhere. Read-only.

    Pass freelancer_id to get just one freelancer's rows; raises
    ValueError when the freelancer does not exist.
    """
    if freelancer_id is not None and not db.get_freelancer(freelancer_id):
        raise ValueError(f"no freelancer #{freelancer_id}")
    cur = db.conn.execute(
        """SELECT f.id AS freelancer_id, f.name AS freelancer_name,
                  f.email, f.phone,
                  i.currency,
                  COUNT(i.id) AS invoice_count,
                  COALESCE(SUM(i.amount_cents), 0) AS invoiced_cents,
                  COALESCE(SUM(
                      (SELECT COALESCE(SUM(p.amount_cents), 0)
                       FROM payments p WHERE p.invoice_id = i.id)
                  ), 0) AS collected_cents
           FROM freelancers f
           LEFT JOIN invoices i
             ON i.freelancer_id = f.id
            AND i.status NOT IN ('draft', 'void')
           WHERE f.id = COALESCE(?, f.id)
           GROUP BY f.id, i.currency
           ORDER BY f.name ASC""",
        (freelancer_id,),
    )
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        d["outstanding_cents"] = d["invoiced_cents"] - d["collected_cents"]
        rows.append(d)
    rows.sort(key=lambda r: (-r["collected_cents"], r["freelancer_name"],
                             r["currency"] or ""))
    return rows


def payments_csv(db: DB) -> str:
    """The full payment ledger as CSV — every recorded payment,
    including payments against invoices that are now paid in full,
    so the export reconciles against bank statements.

    Free-text cells (client, project, method, note) are neutralized
    against CSV formula injection (see `_csv_cell`).
    """
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(PAYMENT_CSV_COLUMNS)
    for p in db.all_payments():
        w.writerow([_csv_cell(v) for v in [
            p["id"], p["invoice_id"], p["client_name"],
            p["project_title"], p["paid_at"], p["method"],
            _units(p["amount_cents"]), p["currency"], p["note"] or "",
        ]])
    return buf.getvalue()
