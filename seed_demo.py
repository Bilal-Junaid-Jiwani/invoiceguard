#!/usr/bin/env python3
"""Seed a demo InvoiceGuard database with clearly-labeled sample data.

Every client/project name carries a "(demo)" suffix so demo data can never be
mistaken for real clients. Covers all invoice states: draft, sent, paid,
paid-late, overdue (with day1 / day1+day7 / day1+day7+day15 escalation), void.

Usage:
    python seed_demo.py [--db PATH] [--fresh]

    --db PATH   SQLite file to write (default: $INVOICEGUARD_DB or
                ~/.invoiceguard/invoiceguard.db)
    --fresh     Delete the target DB file first (demo data only — never use
                this against a real database)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from invoiceguard.web.db import SCHEMA, get_conn


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def day(offset: int) -> str:
    return (datetime.now(timezone.utc).date() + timedelta(days=offset)).isoformat()


def seed(conn) -> None:
    cur = conn.cursor()
    now = iso_now()

    clients = [
        ("Acme Studio (demo)", "accounts@acme-studio.example"),
        ("Brightline Media (demo)", "billing@brightline-media.example"),
        ("Northwind Traders (demo)", "ap@northwind-traders.example"),
    ]
    client_ids = []
    for name, email in clients:
        client_ids.append(
            cur.execute(
                "INSERT INTO clients(name, email, created_at) VALUES (?,?,?)",
                (name, email, now),
            ).lastrowid
        )

    contract = (
        "# Payment Terms (demo contract)\n\n"
        "- Deposit due before work begins.\n"
        "- Late fee 1.5% per month after a 15-day grace period.\n"
        "- Collection notices escalate on day 1, day 7 and day 15 past due.\n"
    )
    projects = [
        # client, title, amount_cents, deposit_pct, late_fee_pct, grace, ack, ack_at
        (client_ids[0], "Website redesign (demo)", 480000, 50.0, 1.5, 15, 1, day(-40)),
        (client_ids[1], "Brand identity sprint (demo)", 260000, 40.0, 2.0, 10, 1, day(-25)),
        (client_ids[2], "API integration (demo)", 720000, 50.0, 1.5, 15, 0, None),
    ]
    project_ids = []
    for cid, title, amt, dep, fee, grace, ack, ack_at in projects:
        project_ids.append(
            cur.execute(
                """INSERT INTO projects(client_id, title, amount_cents, currency,
                       deposit_pct, late_fee_pct, late_fee_grace_days,
                       contract_md, contract_ack, contract_ack_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, title, amt, "USD", dep, fee, grace, contract, ack, ack_at, now),
            ).lastrowid
        )

    p1, p2, p3 = project_ids

    def inv(project_id, kind, cents, status, due=None, sent=None, paid=None,
            stripe_url=None, stripe_session_id=None):
        return cur.execute(
            """INSERT INTO invoices(project_id, kind, amount_cents, currency, status,
                   stripe_url, stripe_session_id, due_date, sent_at, paid_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, kind, cents, "USD", status, stripe_url, stripe_session_id,
             due, sent, paid, now),
        ).lastrowid

    def dunning(invoice_id, stage, sent_day):
        cur.execute(
            "INSERT INTO dunning_events(invoice_id, stage, sent_at) VALUES (?,?,?)",
            (invoice_id, stage, f"{day(sent_day)}T09:00:00+00:00"),
        )

    pay = "https://checkout.stripe.com/pay/cs_test_demo"

    # 1. draft deposit — not sent yet
    inv(p1, "deposit", 240000, "draft")
    # 2. sent milestone, due in the future, has pay link
    inv(p1, "milestone", 120000, "sent", due=day(10), sent=day(-5),
        stripe_url=f"{pay}_001", stripe_session_id="cs_test_demo_001")
    # 3. paid deposit, paid on time
    inv(p1, "deposit", 240000, "paid", due=day(-30), sent=day(-45), paid=day(-32))
    # 4. paid milestone, paid LATE (after due date) → "paid · late" chip
    inv(p2, "milestone", 104000, "paid", due=day(-20), sent=day(-35), paid=day(-12))
    # 5. overdue final, day1 notice only
    i5 = inv(p2, "final", 156000, "overdue", due=day(-3), sent=day(-18),
             stripe_url=f"{pay}_005", stripe_session_id="cs_test_demo_005")
    dunning(i5, "day1", -2)
    # 6. overdue final, day1 + day7 notices
    i6 = inv(p2, "final", 156000, "overdue", due=day(-9), sent=day(-24),
             stripe_url=f"{pay}_006", stripe_session_id="cs_test_demo_006")
    dunning(i6, "day1", -8)
    dunning(i6, "day7", -2)
    # 7. overdue milestone, full escalation day1+day7+day15
    i7 = inv(p3, "milestone", 180000, "overdue", due=day(-22), sent=day(-40),
             stripe_url=f"{pay}_007", stripe_session_id="cs_test_demo_007")
    dunning(i7, "day1", -21)
    dunning(i7, "day7", -15)
    dunning(i7, "day15", -7)
    # 8. void deposit
    inv(p3, "deposit", 360000, "void")
    # 9. sent deposit for webhook test (session id matches test_webhook.py)
    inv(p3, "deposit", 360000, "sent", due=day(14), sent=day(-2),
        stripe_url=f"{pay}_webhook", stripe_session_id="cs_test_demo_webhook_001")

    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser(description="Seed demo InvoiceGuard data")
    ap.add_argument("--db", default=None)
    ap.add_argument("--fresh", action="store_true")
    args = ap.parse_args()

    if args.db:
        os.environ["INVOICEGUARD_DB"] = args.db
    from invoiceguard.web.db import db_path

    path = db_path()
    if args.fresh and path.exists():
        path.unlink()
        print(f"removed {path}")

    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        seed(conn)
    finally:
        conn.close()
    print(f"demo data seeded into {path}")
    print("ALL sample clients/projects are labeled (demo) — not real data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
