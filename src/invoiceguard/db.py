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
    phone TEXT,
    created_at TEXT NOT NULL,
    dunning_day1 INTEGER,
    dunning_day7 INTEGER,
    dunning_day15 INTEGER
);
CREATE TABLE IF NOT EXISTS freelancers(
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
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
    freelancer_id INTEGER REFERENCES freelancers(id),
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
    contract_md TEXT,
    contract_hash TEXT,
    signed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signatures_token
    ON signatures(token);
CREATE INDEX IF NOT EXISTS idx_signatures_project
    ON signatures(project_id);
CREATE TABLE IF NOT EXISTS message_events(
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    channel TEXT NOT NULL DEFAULT 'sms',
    stage TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    provider_sid TEXT,
    sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_events_invoice
    ON message_events(invoice_id, stage, channel);
CREATE TABLE IF NOT EXISTS webhook_events(
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    invoice_id INTEGER REFERENCES invoices(id),
    received_at TEXT NOT NULL
);
"""


def migrate_clients_phone(conn: sqlite3.Connection) -> None:
    """Additive migration: clients.phone (added in v0.5.0).

    CREATE TABLE IF NOT EXISTS never alters an existing table, so
    databases created before v0.5.0 get the column here. Idempotent —
    safe to call on every connect.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)")}
    if "phone" not in cols:
        conn.execute("ALTER TABLE clients ADD COLUMN phone TEXT")
        conn.commit()


# Global dunning stage defaults (days overdue). Also the baseline for
# per-client policies: a NULL override falls back to these. Duplicated
# here (rather than imported from dunning) because dunning imports db.
DUNNING_STAGES = ("day1", "day7", "day15")
DEFAULT_DUNNING_THRESHOLDS = {"day1": 1, "day7": 7, "day15": 15}


def migrate_clients_dunning_policy(conn: sqlite3.Connection) -> None:
    """Additive migration: per-client dunning policy columns (v0.7.0).

    dunning_dayN holds the custom days-overdue threshold for that stage:
    NULL = global default, 0 = stage disabled for this client. Databases
    created before v0.7.0 get the columns here. Idempotent — safe to
    call on every connect.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(clients)")}
    for col in ("dunning_day1", "dunning_day7", "dunning_day15"):
        if col not in cols:
            conn.execute(f"ALTER TABLE clients ADD COLUMN {col} INTEGER")
            conn.commit()


def migrate_invoices_freelancer_id(conn: sqlite3.Connection) -> None:
    """Additive migration: invoices.freelancer_id (agency mode, v0.8.0).

    NULL = unattributed (invoices created before v0.8.0, or never
    assigned). Databases created before v0.8.0 get the column here.
    Idempotent — safe to call on every connect.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
    if "freelancer_id" not in cols:
        conn.execute(
            "ALTER TABLE invoices ADD COLUMN freelancer_id INTEGER "
            "REFERENCES freelancers(id)")
        conn.commit()


def migrate_signatures_contract_md(conn: sqlite3.Connection) -> None:
    """Additive migration: signatures.contract_md (frozen contract snapshot, v0.8.3).

    The signing page renders the contract text the client actually saw,
    and the SHA-256 tamper-evidence hash covers that frozen snapshot —
    not the (possibly later-edited) live project text. Databases created
    before v0.8.3 get the column here; rows created before the column
    existed fall back to the current project text. Idempotent — safe to
    call on every connect.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signatures)")}
    if "contract_md" not in cols:
        conn.execute("ALTER TABLE signatures ADD COLUMN contract_md TEXT")
        conn.commit()


def resolve_dunning_policy(row) -> dict:
    """Resolved dunning thresholds for a client-like row.

    Returns {stage: threshold_days | None}; None means the stage is
    disabled for this client. A NULL column falls back to the global
    default; 0 disables the stage; a positive int is a custom threshold.
    """
    out = {}
    for s in DUNNING_STAGES:
        v = row["dunning_" + s] if row is not None else None
        if v is None:
            out[s] = DEFAULT_DUNNING_THRESHOLDS[s]
        elif int(v) <= 0:
            out[s] = None
        else:
            out[s] = int(v)
    return out


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
        migrate_clients_phone(self.conn)
        migrate_clients_dunning_policy(self.conn)
        migrate_invoices_freelancer_id(self.conn)
        migrate_signatures_contract_md(self.conn)
        backfill_paid_ledger(self.conn)

    # -- clients -----------------------------------------------------
    def add_client(self, name: str, email: str | None = None,
                   phone: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO clients(name, email, phone, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, email, phone, now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def set_client_phone(self, client_id: int, phone: str | None) -> None:
        self.conn.execute(
            "UPDATE clients SET phone = ? WHERE id = ?",
            (phone, client_id),
        )
        self.conn.commit()

    def set_client_email(self, client_id: int, email: str | None) -> None:
        self.conn.execute(
            "UPDATE clients SET email = ? WHERE id = ?",
            (email, client_id),
        )
        self.conn.commit()

    def set_client_dunning_policy(self, client_id: int,
                                  day1: int | None = None,
                                  day7: int | None = None,
                                  day15: int | None = None) -> dict:
        """Set per-client dunning stage thresholds (agency mode).

        Each value: positive int = custom days-overdue threshold for the
        stage, 0 = stage disabled for this client, None = leave unchanged.
        Raises ValueError when the client is missing, a value is not a
        non-negative integer, or the enabled stages would break escalation
        order (day1 -> day7 -> day15 must stay ascending). Returns the
        resolved policy dict ({stage: threshold | None}).
        """
        client = self.get_client(client_id)
        if not client:
            raise ValueError(f"no client #{client_id}")
        updates: dict[str, int] = {}
        for stage, value in (("day1", day1), ("day7", day7),
                             ("day15", day15)):
            if value is None:
                continue
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                raise ValueError(
                    f"dunning threshold for {stage} must be a whole "
                    f"number of days (0 disables the stage)") from None
            if ivalue < 0:
                raise ValueError(
                    f"dunning threshold for {stage} must be >= 0 "
                    f"(0 disables the stage)")
            updates["dunning_" + stage] = ivalue
        # Validate on the merged candidate BEFORE writing anything.
        candidate = dict(client)
        candidate.update(updates)
        resolved = resolve_dunning_policy(candidate)
        enabled = [(s, t) for s, t in resolved.items() if t is not None]
        for (s1, t1), (s2, t2) in zip(enabled, enabled[1:]):
            if t1 > t2:
                raise ValueError(
                    f"dunning escalation must stay day1 -> day7 -> day15 "
                    f"(got {s2}={t2} before {s1}={t1})")
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            self.conn.execute(
                f"UPDATE clients SET {sets} WHERE id = ?",
                (*updates.values(), client_id),
            )
            self.conn.commit()
        return resolved

    def reset_client_dunning_policy(self, client_id: int) -> dict:
        """Clear a client's dunning overrides back to the global defaults."""
        if not self.get_client(client_id):
            raise ValueError(f"no client #{client_id}")
        self.conn.execute(
            "UPDATE clients SET dunning_day1 = NULL, dunning_day7 = NULL, "
            "dunning_day15 = NULL WHERE id = ?",
            (client_id,),
        )
        self.conn.commit()
        return resolve_dunning_policy(self.get_client(client_id))

    def get_client(self, client_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()

    def list_clients(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM clients ORDER BY id"
        ).fetchall()

    # -- freelancers (agency mode) ---------------------------------
    def add_freelancer(self, name: str, email: str | None = None,
                       phone: str | None = None) -> int:
        """Add a freelancer (team member) to the agency roster."""
        name = (name or "").strip()
        if not name:
            raise ValueError("freelancer name is required")
        cur = self.conn.execute(
            "INSERT INTO freelancers(name, email, phone, created_at) "
            "VALUES (?, ?, ?, ?)",
            (name, email, phone, now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def get_freelancer(self, freelancer_id: int) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM freelancers WHERE id = ?", (freelancer_id,)
        ).fetchone()

    def list_freelancers(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM freelancers ORDER BY id"
        ).fetchall()

    def set_freelancer_email(self, freelancer_id: int,
                             email: str | None) -> None:
        self.conn.execute(
            "UPDATE freelancers SET email = ? WHERE id = ?",
            (email, freelancer_id),
        )
        self.conn.commit()

    def set_freelancer_phone(self, freelancer_id: int,
                             phone: str | None) -> None:
        self.conn.execute(
            "UPDATE freelancers SET phone = ? WHERE id = ?",
            (phone, freelancer_id),
        )
        self.conn.commit()

    def assign_invoice_freelancer(self, invoice_id: int,
                                 freelancer_id: int | None) -> None:
        """Attribute an invoice to a freelancer (or None to clear it).

        Attribution is bookkeeping only: it never changes dunning,
        payments, or invoice status. Raises ValueError when the invoice
        or the freelancer does not exist.
        """
        inv = self.get_invoice(invoice_id)
        if not inv:
            raise ValueError(f"no invoice #{invoice_id}")
        if freelancer_id is not None and not self.get_freelancer(
                freelancer_id):
            raise ValueError(f"no freelancer #{freelancer_id}")
        self.conn.execute(
            "UPDATE invoices SET freelancer_id = ? WHERE id = ?",
            (freelancer_id, invoice_id),
        )
        self.conn.commit()

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

        The project's contract text is frozen into the row at creation
        time: the signing page renders this snapshot and the signature
        hash covers it, so later contract edits can't change what the
        client actually signed.
        """
        proj = self.get_project(project_id)
        if not proj:
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
            "INSERT INTO signatures(project_id, token, status, created_at, "
            "contract_md) VALUES (?, ?, 'pending', ?, ?)",
            (project_id, token, now_iso(), proj["contract_md"]),
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
        and the SHA-256 of the contract text the client actually signed
        (tamper evidence: proves *what* was signed). The hashed text is the
        frozen snapshot stored on the signature request at creation time —
        rows created before v0.8.3 have no snapshot and fall back to the
        current project text. Also flips the project's contract_ack to 1 —
        a signed contract IS the acknowledgment.

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
        # Hash the frozen snapshot the client saw, not the live project
        # text (which may have been edited after the link was created).
        signed_text = req.get("contract_md") or proj["contract_md"]
        contract_hash = hashlib.sha256(
            signed_text.encode("utf-8")).hexdigest()
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
            """SELECT i.*, p.title AS project_title, c.name AS client_name,
                      f.name AS freelancer_name
               FROM invoices i
               JOIN projects p ON p.id = i.project_id
               JOIN clients c ON c.id = p.client_id
               LEFT JOIN freelancers f ON f.id = i.freelancer_id
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
                      c.name AS client_name, c.email AS client_email,
                      c.phone AS client_phone,
                      c.dunning_day1, c.dunning_day7, c.dunning_day15,
                      f.name AS freelancer_name
               FROM invoices i
               JOIN projects p ON p.id = i.project_id
               JOIN clients c ON c.id = p.client_id
               LEFT JOIN freelancers f ON f.id = i.freelancer_id
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

    # -- webhook idempotency -------------------------------------------
    def webhook_event_seen(self, event_id: str) -> bool:
        """True if this Stripe event id was already processed.

        Lets the webhook handler drop retried deliveries (Stripe resends
        events until it gets a 2xx) so a replayed partial-payment event
        can never record the same payment twice.
        """
        return self.conn.execute(
            "SELECT 1 FROM webhook_events WHERE event_id = ?",
            (event_id,),
        ).fetchone() is not None

    def mark_webhook_event_processed(self, event_id: str, event_type: str,
                                     invoice_id: int | None = None) -> None:
        """Record that a Stripe event id was handled. Idempotent."""
        self.conn.execute(
            "INSERT OR IGNORE INTO webhook_events"
            "(event_id, event_type, invoice_id, received_at) "
            "VALUES (?, ?, ?, ?)",
            (event_id, event_type, invoice_id, now_iso()),
        )
        self.conn.commit()

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

    # -- message events (SMS / WhatsApp) ------------------------------
    def record_message(self, invoice_id: int, channel: str, stage: str,
                       to_addr: str, provider_sid: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO message_events(invoice_id, channel, stage, "
            "to_addr, provider_sid, sent_at) VALUES (?, ?, ?, ?, ?, ?)",
            (invoice_id, channel, stage, to_addr, provider_sid, now_iso()),
        )
        self.conn.commit()

    def message_sent(self, invoice_id: int, stage: str,
                     channel: str | None = None) -> bool:
        """True when a message event already exists for invoice+stage
        (optionally restricted to one channel). Messages, like dunning
        emails, are sent at most once per stage per invoice."""
        if channel:
            row = self.conn.execute(
                "SELECT 1 FROM message_events WHERE invoice_id = ? "
                "AND stage = ? AND channel = ? LIMIT 1",
                (invoice_id, stage, channel),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT 1 FROM message_events WHERE invoice_id = ? "
                "AND stage = ? LIMIT 1",
                (invoice_id, stage),
            ).fetchone()
        return row is not None

    def messages(self, invoice_id: int) -> list[dict]:
        """Message ledger rows for an invoice, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM message_events WHERE invoice_id = ? "
            "ORDER BY sent_at ASC, id ASC",
            (invoice_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def open_invoices(self) -> list[dict]:
        """Invoices still owed on (sent|overdue|partially-paid) with an
        outstanding balance > 0, oldest due date first (undated last).

        Same 'outstanding' definition as the dashboard totals: invoice
        amount minus the payment-ledger sum. Unlike overdue_invoices()
        this includes not-yet-due invoices — receivables already sent —
        which an aging report needs; drafts, paid, and void invoices
        are excluded.
        """
        rows = self.conn.execute(
            """SELECT i.*, p.title AS project_title, p.late_fee_pct,
                      p.late_fee_grace_days, c.name AS client_name,
                      c.email AS client_email, c.phone AS client_phone,
                      f.name AS freelancer_name,
                      COALESCE((SELECT SUM(p2.amount_cents) FROM payments p2
                                WHERE p2.invoice_id = i.id), 0) AS paid_cents
               FROM invoices i
               JOIN projects p ON p.id = i.project_id
               JOIN clients c ON c.id = p.client_id
               LEFT JOIN freelancers f ON f.id = i.freelancer_id
               WHERE i.status IN ('sent', 'overdue', 'partially-paid')
               ORDER BY (i.due_date IS NULL), i.due_date, i.id"""
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["outstanding_cents"] = d["amount_cents"] - d["paid_cents"]
            if d["outstanding_cents"] > 0:
                out.append(d)
        return out

    def all_payments(self) -> list[dict]:
        """Every payment-ledger row joined with invoice/project/client,
        oldest first — the reconciliation view behind the CSV export."""
        rows = self.conn.execute(
            """SELECT pay.*, i.kind AS invoice_kind, i.currency AS currency,
                      p.title AS project_title, c.name AS client_name
               FROM payments pay
               JOIN invoices i ON i.id = pay.invoice_id
               JOIN projects p ON p.id = i.project_id
               JOIN clients c ON c.id = p.client_id
               ORDER BY pay.paid_at ASC, pay.id ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def overdue_invoices(self) -> list[dict]:
        """Invoices still unpaid (sent|overdue|partially-paid), past due_date,
        with an outstanding balance > 0."""
        rows = self.conn.execute(
            """SELECT i.*, p.title AS project_title, p.late_fee_pct,
                      p.late_fee_grace_days, c.name AS client_name,
                      c.email AS client_email, c.phone AS client_phone,
                      c.dunning_day1, c.dunning_day7, c.dunning_day15,
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
