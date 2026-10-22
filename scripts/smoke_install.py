#!/usr/bin/env python3
"""Fresh-install smoke check.

Builds the wheel, installs it into a CLEAN virtualenv (only the
package's declared dependencies), and runs a real end-to-end flow
with the installed code:

    invoiceguard --version            (console entry point works)
    init -> client add -> project create -> invoice create
                                      (Stripe mocked at the SDK
                                      boundary, like the test suite)
    a failed invoice create           (must exit cleanly and leave
                                      NO orphan draft behind)
    a real signed Stripe webhook      (stripe.Webhook.construct_event
                                      -> StripeObject event -> payment
                                      recorded via the metadata id)

Why this exists (0.8.4): the unit suite runs against the source tree
with hand-built dict events, so it missed that stripe>=15 returns
StripeObject metadata (not a dict) — the production webhook silently
recorded no payments while the suite stayed green. This check runs
the installed artifact the way a user's machine does. It would have
caught that bug.

Usage:  python3 scripts/smoke_install.py [--keep]
Exit code 0 = smoke passed. Requires network for pip (dependencies).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def outer() -> int:
    keep = "--keep" in sys.argv
    work = Path(tempfile.mkdtemp(prefix="ig-smoke-"))
    dist = work / "dist"
    venv = work / "venv"
    print(f"smoke workdir: {work}")

    # 1. Build the wheel (python -m build preferred; pip wheel fallback
    #    so the check also runs in a bare checkout with only pip).
    try:
        _run([sys.executable, "-m", "build", "--wheel", "--outdir", dist],
             cwd=REPO, capture_output=True)
        print("built with: python -m build")
    except subprocess.CalledProcessError:
        _run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
              "-w", dist], cwd=REPO, capture_output=True)
        print("built with: pip wheel (fallback)")
    wheels = list(dist.glob("invoiceguard-*.whl"))
    assert len(wheels) == 1, f"expected one wheel in {dist}, got {wheels}"

    # 2. Clean venv + install ONLY the wheel and its declared deps.
    _run([sys.executable, "-m", "venv", venv])
    vpy = venv / "bin" / "python"
    _run([vpy, "-m", "pip", "install", "-q", "--upgrade", "pip"],
         capture_output=True)
    _run([vpy, "-m", "pip", "install", "-q", wheels[0]], capture_output=True)
    print(f"installed: {wheels[0].name} into clean venv")

    # 3. Run the inner driver with the venv's python.
    _run([vpy, Path(__file__).resolve(), "--inner", work])
    print("SMOKE PASSED")
    if not keep:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    return 0


def inner(work: Path) -> int:
    """Runs INSIDE the clean venv against the installed package."""
    import hashlib
    import hmac
    import json
    import os
    import time
    import types

    home = work / "ig-home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["INVOICEGUARD_CONFIG"] = str(home / "config.yaml")
    os.environ["INVOICEGUARD_DB"] = str(home / "invoiceguard.db")

    import stripe
    from click.testing import CliRunner

    import invoiceguard
    from invoiceguard.cli import cli
    from invoiceguard.db import DB
    from invoiceguard.webhooks import handle_event, verify_event

    print(f"inner: invoiceguard {invoiceguard.__version__}, "
          f"stripe {stripe.VERSION}")

    # Console entry point is installed and reports the same version.
    exe = Path(sys.executable).parent / "invoiceguard"
    out = subprocess.run([str(exe), "--version"], capture_output=True,
                         text=True, check=True).stdout
    assert invoiceguard.__version__ in out, out
    print("ok: console entry point + --version")

    r = CliRunner()
    assert r.invoke(cli, ["init"]).exit_code == 0
    assert r.invoke(cli, ["client", "add", "--name", "Smoke Client",
                          "--email", "smoke@example.test"]).exit_code == 0
    res = r.invoke(cli, ["project", "create", "--client", "Smoke Client",
                         "--title", "Smoke Project", "--amount", "1000"])
    assert res.exit_code == 0, res.output

    # Failed create first (no Stripe key): clean error, no orphan draft.
    db = DB(os.environ["INVOICEGUARD_DB"])
    res = r.invoke(cli, ["invoice", "create", "--project", "1",
                         "--kind", "deposit"])
    assert res.exit_code != 0, res.output
    assert db.list_invoices() == [], "failed create left an orphan draft"
    print("ok: failed invoice create leaves no orphan draft")

    # Successful create with Stripe mocked at the SDK boundary; the CLI
    # stores the Payment Link id (plink_...) like production.
    stripe.PaymentLink.create = lambda **kw: types.SimpleNamespace(
        url="https://buy.stripe.com/smoke", id="plink_smoke")
    os.environ["INVOICEGUARD_STRIPE_SECRET_KEY"] = "sk_test_smoke"
    res = r.invoke(cli, ["invoice", "create", "--project", "1",
                         "--kind", "deposit"])
    assert res.exit_code == 0, res.output
    rows = db.list_invoices()
    assert len(rows) == 1 and rows[0]["stripe_session_id"] == "plink_smoke"
    iid = rows[0]["id"]
    db.close()
    print("ok: invoice create (plink id stored, as in production)")

    # The production webhook path: a REAL signed payload verified by
    # stripe.Webhook.construct_event yields a StripeObject event whose
    # session id is cs_... (never equals the stored plink_...). The
    # payment must be recorded via the metadata invoice id — the exact
    # path that was broken under stripe>=15 before 0.8.4.
    payload = json.dumps({
        "id": "evt_smoke_1",
        "type": "checkout.session.completed",
        "data": {"object": {
            "id": "cs_smoke_1",
            "amount_total": 50_000,
            "payment_status": "paid",
            "metadata": {"invoiceguard_invoice_id": str(iid)},
        }},
    }).encode()
    secret = "whsec_smoke"
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()
    event = verify_event(payload, f"t={ts},v1={sig}", secret)
    assert not isinstance(event, dict), "expected a real StripeObject"
    db = DB(os.environ["INVOICEGUARD_DB"])
    action = handle_event(db, event)
    assert action == "invoice_paid", action
    inv = db.get_invoice(iid)
    assert inv["status"] == "paid", inv["status"]
    assert inv["stripe_session_id"] == "cs_smoke_1"
    db.close()
    print("ok: signed webhook (StripeObject) recorded the payment")
    return 0


if __name__ == "__main__":
    if "--inner" in sys.argv:
        sys.exit(inner(Path(sys.argv[sys.argv.index("--inner") + 1])))
    sys.exit(outer())
