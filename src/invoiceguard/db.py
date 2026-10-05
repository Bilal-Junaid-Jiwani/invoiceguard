"""SQLite persistence.

THIS SCHEMA IS THE CONTRACT shared with the dashboard app — do not
change column names or types without coordinating with the dashboard
worker. Additive migrations only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import secrets

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
CREATE INDEX IF NOT EXISTS idx_invoices_status_due
    ON invoices(status, due_date);
CREATE INDEX IF NOT EXISTS idx_dunning_invoice_stage
    ON dunning_events(invoice_id, stage);
CREATE INDEX IF NOT EXISTS idx_payments_invoice
    ON payments(invoice_id);
CREATE TABLE IF NOT EXISTS signatures(
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    token TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    signer_name TEXT,
    signature_image TEXT,
    contract_hash TEXT,
    signed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signatures_token
    ON signatures(token);
CREATE INDEX IF NOT EXISTS idx_signatures_project
    ON signatures(project_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def backfill_paid_ledger(conn: sqlite3.Connection) -> None:
    """Ledger backfill for invoices marked paid before payments existed.

    Inserts one 'manual' payment row per paid invoice that has no payment
    rows yet, for the full invoice amount dated at paid_at. Idempotent —
    the NOT EXISTS guard means it only ever fills gaps, so it is safe to
    call on every connect.
    """
    conn.execute(
        """
        INSERT INTO payments(invoice_id, amount_cents, method, note, paid_at)
        SELECT i.id, i.amount_cents, 'manual',
               'backfill: invoice was already paid before the ledger existed',
               COALESCE(i.paid_at, i.created_at)
          FROM invoices i
         WHERE i.status = 'paid'
           AND NOT EXISTS (SELECT 1 FROM payments p
                            WHERE p.invoice_id = i.id)
        """
    )
    conn.commit()


class DB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        backfill_paid_ledger(self.conn)

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

    # -- e-signatures ------------------------------------------------
    def create_signature_request(self, project_id: int) -> str:
        """Create (or reuse) a pending signature-request token for a project.

        Returns the token. Only one pending request per project: if one
        already exists its token is returned, so re-running the command
        doesn't orphan old links.
        """
        if not self.get_project(project_id):
            raise ValueError(f"no project #{project_id}")
        row = self.conn.execute(
            "SELECT token FROM signatures WHERE project_id = ? "
            "AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row:
            return row["token"]
        token = secrets.token_urlsafe(32)
        self.conn.execute(
            "INSERT INTO signatures(project_id, token, status, created_at) "
            "VALUES (?, ?, 'pending', ?)",
            (project_id, token, now_iso()),
        )
        self.conn.commit()
        return token

    def get_signature_by_token(self, token: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM signatures WHERE token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None

    def get_signature_for_project(self, project_id: int) -> dict | None:
        """Latest signature record (signed first, then pending) for a project."""
        row = self.conn.execute(
            "SELECT * FROM signatures WHERE project_id = ? "
            "ORDER BY CASE status WHEN 'signed' THEN 0 ELSE 1 END, id DESC "
            "LIMIT 1",
            (project_id,),
        ).fetchone()
        return dict(row) if row else None

    def sign_contract(self, token: str, signer_name: str,
                      signature_image: str) -> dict:
        """Record a signature against the pending request identified by token.

        Stores the signer's typed name, the drawn-signature image (data URL),
        and the SHA-256 of the project's contract text at the moment of
        signing (tamper evidence: proves *what* was signed). Also flips the
        project's contract_ack to 1 — a signed contract IS the acknowledgment.

        Raises ValueError on unknown/used token or invalid input.
        """
        req = self.get_signature_by_token(token)
        if not req:
            raise ValueError("signature request not found or expired")
        if req["status"] != "pending":
            raise ValueError("this signature request was already used")
        signer_name = (signer_name or "").strip()
        if not signer_name:
            raise ValueError("signer name is required")
        if not signature_image or not signature_image.startswith(
                "data:image/"):
            raise ValueError("a drawn signature is required")
        if len(signature_image) > 1_000_000:
            raise ValueError("signature image is too large")
        proj = self.get_project(req["project_id"])
        contract_hash = hashlib.sha256(
            proj["contract_md"].encode("utf-8")).hexdigest()
        signed_at = now_iso()
        self.conn.execute(
            "UPDATE signatures SET status = 'signed', signer_name = ?, "
            "signature_image = ?, contract_hash = ?, signed_at = ? "
            "WHERE id = ?",
            (signer_name, signature_image, contract_hash, signed_at,
             req["id"]),
        )
        self.conn.execute(
            "UPDATE projects SET contract_ack = 1, contract_ack_at = ? "
            "WHERE id = ?",
            (signed_at, req["project_id"]),
        )
        self.conn.commit()
        return {
            "project_id": req["project_id"],
            "signer_name": signer_name,
            "contract_hash": contract_hash,
            "signed_at": signed_at,
        }

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
        """Invoice joined with project + client, as a plain dict.

        Includes the payment ledger summary: paid_cents, outstanding_cents,
        and the list of payments.
        """
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
        if not row:
            return None
        d = dict(row)
        d["paid_cents"] = self.paid_cents(invoice_id)
        d["outstanding_cents"] = d["amount_cents"] - d["paid_cents"]
        d["payments"] = self.payments(invoice_id)
        return d

    # -- payments ----------------------------------------------------
    def payments(self, invoice_id: int) -> list[dict]:
        """Payment ledger rows for an invoice, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM payments WHERE invoice_id = ? "
            "ORDER BY paid_at ASC, id ASC",
            (invoice_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def paid_cents(self, invoice_id: int) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(amount_cents), 0) FROM payments "
            "WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
        return int(row[0])

    def outstanding_cents(self, invoice_id: int) -> int:
        inv = self.get_invoice(invoice_id)
        if not inv:
            raise ValueError(f"no invoice #{invoice_id}")
        return inv["amount_cents"] - self.paid_cents(invoice_id)

    def record_payment(self, invoice_id: int, amount_cents: int,
                       method: str = "manual", note: str | None = None,
                       paid_at: str | None = None) -> dict:
        """Record a payment (partial or full) against an invoice.

        Partial payments move the invoice to status 'partially-paid';
        a payment that clears the balance marks it 'paid' (paid_at set).
        Raises ValueError when the invoice does not exist, is void or
        already fully paid, or when the amount is not positive / exceeds
        the outstanding balance.
        """
        inv = self.get_invoice(invoice_id)
        if not inv:
            raise ValueError(f"no invoice #{invoice_id}")
        if inv["status"] == "void":
            raise ValueError(f"invoice #{invoice_id} is void")
        if inv["status"] == "paid":
            raise ValueError(f"invoice #{invoice_id} is already paid in full")
        if amount_cents <= 0:
            raise ValueError("payment amount must be positive")
        outstanding = inv["amount_cents"] - self.paid_cents(invoice_id)
        if amount_cents > outstanding:
            raise ValueError(
                f"payment of {amount_cents / 100:.2f} exceeds the outstanding "
                f"balance of {outstanding / 100:.2f}"
            )
        paid_at = paid_at or now_iso()
        self.conn.execute(
            "INSERT INTO payments(invoice_id, amount_cents, method, note, "
            "paid_at) VALUES (?, ?, ?, ?, ?)",
            (invoice_id, amount_cents, method, note, paid_at),
        )
        remaining = outstanding - amount_cents
        if remaining == 0:
            self.conn.execute(
                "UPDATE invoices SET status = 'paid', paid_at = ? "
                "WHERE id = ?",
                (paid_at, invoice_id),
            )
            status = "paid"
        else:
            self.conn.execute(
                "UPDATE invoices SET status = 'partially-paid' WHERE id = ?",
                (invoice_id,),
            )
            status = "partially-paid"
        self.conn.commit()
        return {
            "invoice_id": invoice_id,
            "paid_cents": amount_cents,
            "outstanding_cents": remaining,
            "status": status,
        }

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
        """Invoices still unpaid (sent|overdue|partially-paid), past due_date,
        with an outstanding balance > 0."""
        rows = self.conn.execute(
            """SELECT i.*, p.title AS project_title, p.late_fee_pct,
                      p.late_fee_grace_days, c.name AS client_name,
                      c.email AS client_email,
                      COALESCE((SELECT SUM(p2.amount_cents) FROM payments p2
                                WHERE p2.invoice_id = i.id), 0) AS paid_cents
               FROM invoices i
               JOIN projects p ON p.id = i.project_id
               JOIN clients c ON c.id = p.client_id
               WHERE i.status IN ('sent', 'overdue', 'partially-paid')
                 AND i.due_date IS NOT NULL
                 AND date(i.due_date) <= date('now')
               ORDER BY i.due_date"""
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["outstanding_cents"] = d["amount_cents"] - d["paid_cents"]
            if d["outstanding_cents"] > 0:
                out.append(d)
        return out

    def close(self) -> None:
        self.conn.close()
