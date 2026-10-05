"""invoiceguard CLI — freelancer payment enforcement."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import click

from . import __version__
from .config import (CONFIG_TEMPLATE, config_path, db_path, load_config,
                     templates_dir)
from .contracts import render_contract
from .db import DB
from .dunning import check_due, money
from .stripe_links import create_payment_link, default_due_date

TEMPLATE_SOURCE = Path(__file__).resolve().parent / "email_templates"


def _db() -> DB:
    return DB(db_path())


@click.group()
@click.version_option(__version__)
def cli():
    """InvoiceGuard — deposit links + contract, then automated collections."""


# -- init ----------------------------------------------------------------
@cli.command()
def init():
    """Create ~/.invoiceguard (config template, DB, email templates)."""
    cfg_path = config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
        click.echo(f"wrote config template: {cfg_path}")
    else:
        click.echo(f"config already exists: {cfg_path}")

    db = DB(db_path())  # creates tables
    db.close()
    click.echo(f"database ready: {db_path()}")

    tdir = templates_dir()
    tdir.mkdir(parents=True, exist_ok=True)
    for src in TEMPLATE_SOURCE.glob("*.md"):
        dest = tdir / src.name
        if not dest.exists():
            shutil.copy(src, dest)
            click.echo(f"installed template: {dest}")
    click.echo("done. Edit the config, then add a Stripe test key.")


# -- client ---------------------------------------------------------------
@cli.group()
def client():
    """Manage clients."""


@client.command("add")
@click.option("--name", required=True, help="Client name")
@click.option("--email", default=None, help="Client email (for dunning)")
def client_add(name, email):
    """Add a client."""
    db = _db()
    cid = db.add_client(name, email)
    db.close()
    click.echo(f"client #{cid}: {name} <{email or 'no email'}>")


@client.command("list")
def client_list():
    """List clients."""
    db = _db()
    rows = db.list_clients()
    db.close()
    for r in rows:
        click.echo(f"#{r['id']:>3}  {r['name']:<30} {r['email'] or ''}")


# -- project --------------------------------------------------------------
@cli.group()
def project():
    """Manage projects (contract + terms)."""


@project.command("create")
@click.option("--client", "client_name", required=True,
              help="Client name (must already exist)")
@click.option("--title", required=True, help="Project title")
@click.option("--amount", type=float, required=True,
              help="Total project amount in currency units, e.g. 2000")
@click.option("--currency", default="USD")
@click.option("--deposit-pct", type=float, default=50.0)
@click.option("--late-fee-pct", type=float, default=1.5)
@click.option("--late-fee-grace-days", type=int, default=15)
@click.option("--ack", is_flag=True, default=False,
              help="Record the contract as acknowledged by the client")
def project_create(client_name, title, amount, currency, deposit_pct,
                   late_fee_pct, late_fee_grace_days, ack):
    """Create a project and generate its contract (with late-fee clause)."""
    db = _db()
    clients = [c for c in db.list_clients() if c["name"] == client_name]
    if not clients:
        db.close()
        raise click.ClickException(
            f'no client named "{client_name}" — add them first '
            f"with `invoiceguard client add`"
        )
    client = clients[0]
    amount_cents = round(amount * 100)
    contract = render_contract(
        client["name"], client["email"], title, amount_cents, currency,
        deposit_pct, late_fee_pct, late_fee_grace_days,
    )
    pid = db.add_project(
        client["id"], title, amount_cents, currency, deposit_pct,
        late_fee_pct, late_fee_grace_days, contract,
    )
    if ack:
        db.ack_contract(pid)
    db.close()
    click.echo(f"project #{pid}: {title}")
    click.echo(f"  client: {client['name']}")
    click.echo(f"  amount: {money(amount_cents, currency)} "
               f"(deposit {deposit_pct}% = "
               f"{money(round(amount_cents * deposit_pct / 100), currency)})")
    click.echo(f"  late fee: {late_fee_pct}%/mo after {late_fee_grace_days} days grace")
    click.echo(f"  contract acknowledged: {'yes' if ack else 'no — run `invoiceguard project ack ' + str(pid) + '` after the client accepts'}")
    click.echo("  --- contract preview ---")
    click.echo(contract)


@project.command("list")
def project_list():
    """List projects."""
    db = _db()
    rows = db.list_projects()
    db.close()
    for r in rows:
        ack = "ack" if r["contract_ack"] else "no-ack"
        click.echo(f"#{r['id']:>3}  {r['title']:<35} {r['client_name']:<20} "
                   f"{money(r['amount_cents'], r['currency']):>12} [{ack}]")


@project.command("ack")
@click.argument("project_id", type=int)
def project_ack(project_id):
    """Record client acknowledgment of the contract (paid deposit link)."""
    db = _db()
    if not db.get_project(project_id):
        db.close()
        raise click.ClickException(f"no project #{project_id}")
    db.ack_contract(project_id)
    db.close()
    click.echo(f"project #{project_id}: contract acknowledged at "
               f"{datetime.now(timezone.utc).isoformat()}")


@project.command("sign-request")
@click.argument("project_id", type=int)
def project_sign_request(project_id):
    """Create a one-time e-signature link for the project's contract.

    Prints a /sign/<token> URL — send it to the client while
    `invoiceguard dashboard` is running. The client reads the contract,
    types their name and draws a signature; the signed text is hashed
    (SHA-256) and stored as tamper evidence. Re-running for the same
    project returns the existing pending link.
    """
    db = _db()
    if not db.get_project(project_id):
        db.close()
        raise click.ClickException(f"no project #{project_id}")
    token = db.create_signature_request(project_id)
    db.close()
    click.echo(f"project #{project_id}: signature request created")
    click.echo(f"  signing link: http://127.0.0.1:8000/sign/{token}")
    click.echo("  (works while `invoiceguard dashboard` is running; "
               "single-use, expires when signed)")


@project.command("sign-status")
@click.argument("project_id", type=int)
def project_sign_status(project_id):
    """Show the e-signature status of a project's contract."""
    db = _db()
    proj = db.get_project(project_id)
    if not proj:
        db.close()
        raise click.ClickException(f"no project #{project_id}")
    sig = db.get_signature_for_project(project_id)
    db.close()
    if not sig:
        click.echo(f"project #{project_id}: no signature request yet — "
                   f"run `invoiceguard project sign-request {project_id}`")
    elif sig["status"] == "signed":
        click.echo(f"project #{project_id}: SIGNED")
        click.echo(f"  signer:      {sig['signer_name']}")
        click.echo(f"  signed at:   {sig['signed_at']}")
        click.echo(f"  contract sha256: {sig['contract_hash']}")
    else:
        click.echo(f"project #{project_id}: awaiting signature")
        click.echo(f"  signing link: http://127.0.0.1:8000/sign/{sig['token']}")


# -- invoice --------------------------------------------------------------
@cli.group()
def invoice():
    """Manage invoices (Stripe payment links)."""


@invoice.command("create")
@click.option("--project", "project_id", type=int, required=True)
@click.option("--kind", type=click.Choice(["deposit", "milestone", "final"]),
              required=True)
@click.option("--amount", type=float, default=None,
              help="Override amount in currency units (default: kind-based)")
@click.option("--due-days", type=int, default=7,
              help="Days until due (default 7)")
def invoice_create(project_id, kind, amount, due_days):
    """Create a Stripe payment link for a deposit/milestone/final invoice."""
    db = _db()
    proj = db.get_project(project_id)
    if not proj:
        db.close()
        raise click.ClickException(f"no project #{project_id}")

    if amount is None:
        if kind == "deposit":
            amount_cents = round(proj["amount_cents"] * proj["deposit_pct"] / 100)
        elif kind == "final":
            amount_cents = proj["amount_cents"] - round(
                proj["amount_cents"] * proj["deposit_pct"] / 100)
        else:  # milestone
            amount_cents = proj["amount_cents"]
    else:
        amount_cents = round(amount * 100)

    due = (datetime.now(timezone.utc).date()
           ).isoformat() if due_days <= 0 else default_due_date(due_days)
    iid = db.add_invoice(project_id, kind, amount_cents, proj["currency"],
                         status="draft", due_date=due)

    cfg = load_config()
    try:
        inv = db.invoice_full(iid)
        url, link_id = create_payment_link(inv, cfg.get("stripe_secret_key", ""))
    except ValueError as e:
        db.close()
        raise click.ClickException(str(e))

    db.update_invoice(
        iid,
        status="sent",
        stripe_url=url,
        stripe_session_id=link_id,
        sent_at=datetime.now(timezone.utc).isoformat(),
    )
    db.close()
    click.echo(f"invoice #{iid} ({kind}): {money(amount_cents, proj['currency'])}")
    click.echo(f"  pay link: {url}")
    click.echo(f"  due: {due}   status: sent")


@invoice.command("list")
def invoice_list():
    """List invoices."""
    db = _db()
    rows = db.list_invoices()
    db.close()
    for r in rows:
        click.echo(f"#{r['id']:>3}  {r['kind']:<9} {r['status']:<14} "
                   f"{money(r['amount_cents'], r['currency']):>12}  "
                   f"{r['project_title']:<30} due {r['due_date'] or '-'}")


@invoice.command("record-payment")
@click.argument("invoice_id", type=int)
@click.option("--amount", type=float, required=True,
              help="Payment amount in currency units (e.g. 250.00)")
@click.option("--note", default=None,
              help="Optional note (check number, bank reference, ...)")
@click.option("--method", default="manual", show_default=True,
              help="Payment method label (manual, bank, stripe, ...)")
def invoice_record_payment(invoice_id, amount, note, method):
    """Record a (partial) payment against an invoice.

    A payment that clears the outstanding balance marks the invoice paid;
    otherwise the invoice becomes 'partially-paid' and dunning continues
    against the remaining balance.
    """
    db = _db()
    inv = db.get_invoice(invoice_id)
    if not inv:
        db.close()
        raise click.ClickException(f"no invoice #{invoice_id}")
    cents = round(amount * 100)
    try:
        res = db.record_payment(invoice_id, cents, method=method, note=note)
    except ValueError as e:
        db.close()
        raise click.ClickException(str(e))
    db.close()
    if res["status"] == "paid":
        click.echo(f"invoice #{invoice_id}: recorded "
                   f"{money(cents, inv['currency'])} — paid in full")
    else:
        click.echo(f"invoice #{invoice_id}: recorded "
                   f"{money(cents, inv['currency'])} — "
                   f"{money(res['outstanding_cents'], inv['currency'])} "
                   f"still outstanding")


@invoice.command("mark-paid")
@click.argument("invoice_id", type=int)
def invoice_mark_paid(invoice_id):
    """Manually mark an invoice as paid (records the full outstanding
    balance in the payment ledger)."""
    db = _db()
    inv = db.get_invoice(invoice_id)
    if not inv:
        db.close()
        raise click.ClickException(f"no invoice #{invoice_id}")
    try:
        res = db.record_payment(invoice_id,
                               db.outstanding_cents(invoice_id),
                               method="manual")
    except ValueError as e:
        db.close()
        raise click.ClickException(str(e))
    db.close()
    click.echo(f"invoice #{invoice_id} marked paid "
               f"({money(res['paid_cents'], inv['currency'])})")


@invoice.command("void")
@click.argument("invoice_id", type=int)
def invoice_void(invoice_id):
    """Void an invoice."""
    db = _db()
    inv = db.get_invoice(invoice_id)
    if not inv:
        db.close()
        raise click.ClickException(f"no invoice #{invoice_id}")
    db.update_invoice(invoice_id, status="void")
    db.close()
    click.echo(f"invoice #{invoice_id} voided")


# -- dunning ---------------------------------------------------------------
@cli.command("check-due")
def check_due_cmd():
    """Scan for overdue invoices and send the next dunning stage."""
    db = _db()
    cfg = load_config()
    smtp_cfg = cfg.get("smtp") or {}
    if not smtp_cfg.get("host") or "example.com" in str(smtp_cfg.get("host")):
        db.close()
        raise click.ClickException(
            "SMTP is not configured — set smtp.host etc. in "
            f"{config_path()} first."
        )
    results = check_due(db, smtp_cfg)
    db.close()
    if not results:
        click.echo("nothing due — no emails sent")
    for r in results:
        click.echo(f"sent {r['stage']} to {r['to']} "
                   f"(invoice #{r['invoice_id']})")


# -- dashboard -------------------------------------------------------------
@cli.command()
@click.option("--port", type=int, default=8000, show_default=True,
              help="Port to listen on")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Host to bind (keep 127.0.0.1: localhost only)")
def dashboard(port, host):
    """Launch the InvoiceGuard web dashboard (local web UI)."""
    import uvicorn

    from .web.main import app

    click.echo(f"InvoiceGuard dashboard: http://{host}:{port}/")
    uvicorn.run(app, host=host, port=port)


def main():
    cli()
