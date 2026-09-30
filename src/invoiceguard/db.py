"""SQLite persistence.

THIS SCHEMA IS THE CONTRACT shared with the dashboard app — do not
change column names or types without coordinating with the dashboard
worker. Additive migrations only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
CREATE INDEX IF NOT EXISTS idx_invoices_status_due
    ON invoices(status, due_date);
CREATE INDEX IF NOT EXISTS idx_dunning_invoice_stage
    ON dunning_events(invoice_id, stage);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    # -- clients -----------------------------------------------------
    def add_client(self, name: str, email: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO clients(name, email, created_at) VALUES (?, ?, ?)",
            (name, email, now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_client(self, client_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()

    def list_clients(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM clients ORDER BY id"
        ).fetchall()

    # -- projects ----------------------------------------------------
    def add_project(self, client_id: int, title: str, amount_cents: int,
                    currency: str = "USD", deposit_pct: float = 50.0,
                    late_fee_pct: float = 1.5, late_fee_grace_days: int = 15,
                    contract_md: str = "") -> int:
        cur = self.conn.execute(
            """INSERT INTO projects(client_id, title, amount_cents, currency,
               deposit_pct, late_fee_pct, late_fee_grace_days, contract_md,
               created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client_id, title, amount_cents, currency, deposit_pct,
             late_fee_pct, late_fee_grace_days, contract_md, now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_project(self, project_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ).fetchone()

    def list_projects(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT p.*, c.name AS client_name
               FROM projects p JOIN clients c ON c.id = p.client_id
               ORDER BY p.id"""
        ).fetchall()

    def ack_contract(self, project_id: int) -> None:
        self.conn.execute(
            "UPDATE projects SET contract_ack = 1, contract_ack_at = ? "
            "WHERE id = ?",
            (now_iso(), project_id),
        )
        self.conn.commit()

    # -- invoices ----------------------------------------------------
    def add_invoice(self, project_id: int, kind: str, amount_cents: int,
                    currency: str = "USD", status: str = "draft",
                    due_date: str | None = None) -> int:
        cur = self.conn.execute(
            """INSERT INTO invoices(project_id, kind, amount_cents, currency,
               status, due_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (project_id, kind, amount_cents, currency, status, due_date,
             now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_invoice(self, invoice_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
        ).fetchone()

    def list_invoices(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT i.*, p.title AS project_title, c.name AS client_name
               FROM invoices i
               JOIN projects p ON p.id = i.project_id
               JOIN clients c ON c.id = p.client_id
               ORDER BY i.id"""
        ).fetchall()

    def update_invoice(self, invoice_id: int, **fields) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(
            f"UPDATE invoices SET {sets} WHERE id = ?",
            (*fields.values(), invoice_id),
        )
        self.conn.commit()

    def invoice_full(self, invoice_id: int) -> dict | None:
        """Invoice joined with project + client, as a plain dict."""
        row = self.conn.execute(
            """SELECT i.*, p.title AS project_title, p.late_fee_pct,
                      p.late_fee_grace_days, p.contract_ack,
                      c.name AS client_name, c.email AS client_email
               FROM invoices i
               JOIN projects p ON p.id = i.project_id
               JOIN clients c ON c.id = p.client_id
               WHERE i.id = ?""",
            (invoice_id,),
        ).fetchone()
        return dict(row) if row else None

    # -- dunning -----------------------------------------------------
    def record_dunning(self, invoice_id: int, stage: str) -> None:
        self.conn.execute(
            "INSERT INTO dunning_events(invoice_id, stage, sent_at) "
            "VALUES (?, ?, ?)",
            (invoice_id, stage, now_iso()),
        )
        self.conn.commit()

    def sent_stages(self, invoice_id: int) -> set[str]:
        rows = self.conn.execute(
            "SELECT stage FROM dunning_events WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchall()
        return {r["stage"] for r in rows}

    def overdue_invoices(self) -> list[dict]:
        """Invoices still (sent|overdue) and past due_date."""
        rows = self.conn.execute(
            """SELECT i.*, p.title AS project_title, p.late_fee_pct,
                      p.late_fee_grace_days, c.name AS client_name,
                      c.email AS client_email
               FROM invoices i
               JOIN projects p ON p.id = i.project_id
               JOIN clients c ON c.id = p.client_id
               WHERE i.status IN ('sent', 'overdue')
                 AND i.due_date IS NOT NULL
                 AND date(i.due_date) <= date('now')
               ORDER BY i.due_date"""
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
