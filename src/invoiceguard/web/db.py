"""SQLite access layer for the InvoiceGuard dashboard.

Contract: reads the EXACT schema from the InvoiceGuard brief (shared with the
backend worker). The dashboard never writes business data — it only reads, and
creates empty tables (CREATE TABLE IF NOT EXISTS) so empty states render when
the DB is fresh. The Stripe webhook handler is the sole writer (paid marking).

DB path: $INVOICEGUARD_DB, else ~/.invoiceguard/invoiceguard.db
Money is stored in cents; display helpers convert to dollars.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from ..db import backfill_paid_ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY,
  client_id INTEGER NOT NULL REFERENCES clients(id),
  title TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  deposit_pct REAL NOT NULL DEFAULT 50.0,
  late_fee_pct REAL NOT NULL DEFAULT 1.5,
  late_fee_grace_days INTEGER NOT NULL DEFAULT 15,
  contract_md TEXT NOT NULL,
  contract_ack INTEGER NOT NULL DEFAULT 0,
  contract_ack_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id),
  kind TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL DEFAULT 'draft',
  stripe_url TEXT,
  stripe_session_id TEXT,
  due_date TEXT,
  sent_at TEXT,
  paid_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dunning_events(
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  stage TEXT NOT NULL,
  sent_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES invoices(id),
  amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
  method TEXT NOT NULL DEFAULT 'manual',
  note TEXT,
  paid_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_payments_invoice
  ON payments(invoice_id);
"""

STAGE_ORDER = {"day1": 1, "day7": 2, "day15": 3}
STAGE_RANK_TO_LABEL = {v: k for k, v in STAGE_ORDER.items()}
STAGE_DISPLAY = {"day1": "Day 1", "day7": "Day 7", "day15": "Day 15"}

STATUSES = ("draft", "sent", "partially-paid", "paid", "overdue", "void")
UNPAID = ("sent", "overdue", "partially-paid")


def db_path() -> Path:
    p = os.environ.get("INVOICEGUARD_DB")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".invoiceguard" / "invoiceguard.db"


def get_conn() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        backfill_paid_ledger(conn)
        conn.commit()
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def format_money(cents: int | None, currency: str = "USD") -> str:
    if cents is None:
        return "—"
    value = cents / 100
    grouped = f"{value:,.2f}"
    if (currency or "USD").upper() == "USD":
        return f"${grouped}"
    return f"{grouped} {currency}"


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def days_overdue(due_date: str | None, status: str) -> int:
    """Days past due for an unpaid invoice; 0 when not overdue."""
    if status not in UNPAID:
        return 0
    due = _parse_day(due_date)
    if not due:
        return 0
    delta = (today_utc() - due).days
    return delta if delta > 0 else 0


def paid_late(due_date: str | None, paid_at: str | None) -> bool:
    due = _parse_day(due_date)
    paid = _parse_day(paid_at)
    return bool(due and paid and paid > due)


def list_invoices(status: str | None = None) -> list[dict]:
    conn = get_conn()
    try:
        q = """
        SELECT i.id, i.kind, i.amount_cents, i.currency, i.status,
               i.stripe_url, i.due_date, i.sent_at, i.paid_at, i.created_at,
               COALESCE((SELECT SUM(p.amount_cents) FROM payments p
                          WHERE p.invoice_id = i.id), 0) AS paid_cents,
               p.title AS project_title,
               c.name AS client_name,
               (SELECT MAX(CASE de.stage
                            WHEN 'day1' THEN 1
                            WHEN 'day7' THEN 2
                            WHEN 'day15' THEN 3
                            ELSE 0 END)
                  FROM dunning_events de WHERE de.invoice_id = i.id) AS stage_rank
          FROM invoices i
          JOIN projects p ON p.id = i.project_id
          JOIN clients c ON c.id = p.client_id
        """
        params: list = []
        if status:
            q += " WHERE i.status = ?"
            params.append(status)
        q += " ORDER BY i.created_at DESC, i.id DESC"
        rows = conn.execute(q, params).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        rank = d.pop("stage_rank") or 0
        d["stage"] = STAGE_RANK_TO_LABEL.get(rank)  # None when no escalation yet
        d["days_overdue"] = days_overdue(d["due_date"], d["status"])
        d["paid_late"] = d["status"] == "paid" and paid_late(d["due_date"], d["paid_at"])
        d["outstanding_cents"] = d["amount_cents"] - d["paid_cents"]
        out.append(d)
    return out


def status_counts() -> dict[str, int]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM invoices GROUP BY status"
        ).fetchall()
    finally:
        conn.close()
    counts = {s: 0 for s in STATUSES}
    for r in rows:
        if r["status"] in counts:
            counts[r["status"]] = r["n"]
    return counts


def totals() -> dict:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN i.status IN ('sent','overdue','partially-paid')
                                THEN i.amount_cents - COALESCE(p.paid, 0) END), 0) AS outstanding,
              COALESCE(SUM(CASE WHEN i.status = 'overdue'
                                THEN i.amount_cents - COALESCE(p.paid, 0) END), 0) AS overdue,
              COALESCE((SELECT SUM(amount_cents) FROM payments), 0) AS collected,
              COUNT(DISTINCT i.currency) AS currencies
              FROM invoices i
              LEFT JOIN (SELECT invoice_id, SUM(amount_cents) AS paid
                           FROM payments GROUP BY invoice_id) p
                     ON p.invoice_id = i.id
            """
        ).fetchone()
    finally:
        conn.close()
    return dict(row)


def get_invoice(invoice_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute(
            """
            SELECT i.*,
                   COALESCE((SELECT SUM(p.amount_cents) FROM payments p
                              WHERE p.invoice_id = i.id), 0) AS paid_cents,
                   p.title AS project_title, p.amount_cents AS project_amount_cents,
                   p.currency AS project_currency, p.deposit_pct, p.late_fee_pct,
                   p.late_fee_grace_days, p.contract_ack, p.contract_ack_at,
                   c.name AS client_name, c.email AS client_email,
                   (SELECT MAX(CASE de.stage
                                WHEN 'day1' THEN 1
                                WHEN 'day7' THEN 2
                                WHEN 'day15' THEN 3
                                ELSE 0 END)
                      FROM dunning_events de WHERE de.invoice_id = i.id) AS stage_rank
              FROM invoices i
              JOIN projects p ON p.id = i.project_id
              JOIN clients c ON c.id = p.client_id
             WHERE i.id = ?
            """,
            (invoice_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    rank = d.pop("stage_rank") or 0
    d["stage"] = STAGE_RANK_TO_LABEL.get(rank)
    d["days_overdue"] = days_overdue(d["due_date"], d["status"])
    d["paid_late"] = d["status"] == "paid" and paid_late(d["due_date"], d["paid_at"])
    d["outstanding_cents"] = d["amount_cents"] - d["paid_cents"]
    return d


def dunning_events(invoice_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT stage, sent_at FROM dunning_events WHERE invoice_id = ? ORDER BY sent_at ASC",
            (invoice_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def payments(invoice_id: int) -> list[dict]:
    """Payment ledger for one invoice, oldest first."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT amount_cents, method, note, paid_at FROM payments "
            "WHERE invoice_id = ? ORDER BY paid_at ASC, id ASC",
            (invoice_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def build_timeline(inv: dict, events: list[dict],
                   payments: list[dict] | tuple = ()) -> list[dict]:
    """Chronological timeline: contract ack → sent → payments → dunning
    stages → paid."""
    tl: list[dict] = []
    if inv.get("contract_ack") and inv.get("contract_ack_at"):
        tl.append({"label": "Contract acknowledged", "at": inv["contract_ack_at"]})
    if inv.get("sent_at"):
        tl.append({"label": "Invoice sent", "at": inv["sent_at"]})
    for p in payments:
        label = (
            f"Payment received — {format_money(p['amount_cents'], inv.get('currency', 'USD'))}"
        )
        if p.get("method") and p["method"] != "manual":
            label += f" ({p['method']})"
        tl.append({"label": label, "at": p["paid_at"]})
    for e in events:
        tl.append(
            {
                "label": f"Escalation — {STAGE_DISPLAY.get(e['stage'], e['stage'])} notice",
                "at": e["sent_at"],
            }
        )
    if inv.get("paid_at"):
        tl.append({"label": "Paid", "at": inv["paid_at"]})
    tl.sort(key=lambda e: e["at"] or "")
    return tl


def mark_invoice_paid(stripe_session_id: str, invoice_id_hint: int | None = None,
                      amount_cents: int | None = None) -> int | None:
    """Mark the invoice for a completed checkout session as paid.

    Looks up by explicit metadata invoice id first, then by stripe_session_id
    column. Records the payment in the ledger (method 'stripe') — the amount
    defaults to the full outstanding balance when the session does not report
    one. Returns the invoice id marked, or None when nothing matched.
    """
    conn = get_conn()
    try:
        target: int | None = None
        if invoice_id_hint is not None:
            row = conn.execute(
                "SELECT id, status FROM invoices WHERE id = ?", (invoice_id_hint,)
            ).fetchone()
            if row:
                target = row["id"]
        if target is None and stripe_session_id:
            row = conn.execute(
                "SELECT id FROM invoices WHERE stripe_session_id = ?",
                (stripe_session_id,),
            ).fetchone()
            if row:
                target = row["id"]
        if target is None:
            return None
        inv = conn.execute(
            "SELECT id, amount_cents, status, paid_at FROM invoices WHERE id = ?",
            (target,),
        ).fetchone()
        if inv["status"] == "paid":
            return target  # idempotent: already fully paid
        already = conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM payments "
            "WHERE invoice_id = ?",
            (target,),
        ).fetchone()[0]
        outstanding = inv["amount_cents"] - int(already)
        if amount_cents is None:
            amount = outstanding
        else:
            amount = max(0, min(amount_cents, outstanding))
        if amount > 0:
            conn.execute(
                "INSERT INTO payments(invoice_id, amount_cents, method, note, "
                "paid_at) VALUES (?, ?, 'stripe', ?, ?)",
                (target, amount, f"stripe session {stripe_session_id}",
                 now_iso()),
            )
        if amount >= outstanding:
            conn.execute(
                "UPDATE invoices SET status = 'paid', paid_at = ?, "
                "stripe_session_id = ? WHERE id = ?",
                (now_iso(), stripe_session_id, target),
            )
        else:
            conn.execute(
                "UPDATE invoices SET status = 'partially-paid', "
                "stripe_session_id = ? WHERE id = ?",
                (stripe_session_id, target),
            )
        conn.commit()
        return target
    finally:
        conn.close()
