"""0.41.0 regression tests — signing a contract is atomically single-use.

Finding 3 of the 2026-11-23 security audit: sign_contract (both the
web layer and the core DB) read status='pending', then ran an UPDATE
with no status guard — two submissions interleaved between the check
and the write BOTH succeeded and issued conflicting success receipts
for one single-use link, the stored signer being whoever committed
last. The UPDATE now re-tests status='pending' and the loser is
rejected with "already used" (rowcount 0). These tests hold the
check->act window open (a slowed contract hash) so the interleaving
is deterministic rather than timing luck: exactly one signer wins.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
import time
import types

IMG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"

_real_sha256 = hashlib.sha256


def _slow_sha256(data=b"", **kwargs):
    time.sleep(0.3)
    return _real_sha256(data, **kwargs)


def _race(fn_a, fn_b) -> dict:
    """Run two callables from a common barrier; collect (ok|err, value)."""
    barrier = threading.Barrier(2)
    results: dict[str, tuple[str, object]] = {}

    def run(key, fn):
        barrier.wait(timeout=10)
        try:
            results[key] = ("ok", fn())
        except Exception as e:  # recorded, then asserted on below
            results[key] = ("err", f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=run, args=("a", fn_a)),
               threading.Thread(target=run, args=("b", fn_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def _assert_single_winner(results: dict) -> str:
    outcomes = sorted(v[0] for v in results.values())
    assert outcomes == ["err", "ok"], results
    err = next(v[1] for v in results.values() if v[0] == "err")
    assert "already used" in err
    winner = next(v[1] for v in results.values() if v[0] == "ok")
    return winner["signer_name"]


def test_web_sign_contract_concurrent_signers_single_winner(
        tmp_path, monkeypatch):
    db_file = str(tmp_path / "web.db")
    monkeypatch.setenv("INVOICEGUARD_DB", db_file)
    from invoiceguard.web import db as wdb
    wdb.init_db()
    now = "2026-09-30T10:00:00+00:00"
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO clients(name, email, created_at) VALUES (?,?,?)",
        ("Test Client", "billing@example.com", now))
    conn.execute(
        """INSERT INTO projects(client_id, title, amount_cents, currency,
               contract_md, created_at) VALUES (?,?,?,?,?,?)""",
        (1, "Test Project", 100_000, "USD", "# contract", now))
    conn.execute(
        """INSERT INTO signatures(project_id, token, status, contract_md,
               created_at) VALUES (?,?,?,?,?)""",
        (1, "tok_race", "pending", "# contract", now))
    conn.commit()
    conn.close()

    # hold the check->act window open so both threads pass the
    # status read before either writes (deterministic interleaving)
    monkeypatch.setattr(wdb, "hashlib",
                        types.SimpleNamespace(sha256=_slow_sha256))
    results = _race(
        lambda: wdb.sign_contract("tok_race", "Alice A", IMG),
        lambda: wdb.sign_contract("tok_race", "Bob B", IMG),
    )
    winner_name = _assert_single_winner(results)
    stored = wdb.get_signature_request("tok_race")
    assert stored["status"] == "signed"
    assert stored["signer_name"] == winner_name


def test_core_sign_contract_concurrent_signers_single_winner(
        home, db, sample_project, monkeypatch):
    token = db.create_signature_request(sample_project["id"])
    path = os.environ["INVOICEGUARD_DB"]

    from invoiceguard import db as core_db_module
    monkeypatch.setattr(core_db_module, "hashlib",
                        types.SimpleNamespace(sha256=_slow_sha256))

    def sign(name):
        d = core_db_module.DB(path)
        try:
            return d.sign_contract(token, name, IMG)
        finally:
            d.close()

    results = _race(lambda: sign("Alice A"), lambda: sign("Bob B"))
    winner_name = _assert_single_winner(results)
    stored = db.get_signature_by_token(token)
    assert stored["status"] == "signed"
    assert stored["signer_name"] == winner_name
