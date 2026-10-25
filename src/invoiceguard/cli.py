"""invoiceguard CLI — freelancer payment enforcement."""

from __future__ import annotations

import shutil
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import click

from . import __version__
from .config import (CONFIG_TEMPLATE, config_path, db_path, load_config,
                     templates_dir)
from .contracts import render_contract
from .db import DB, pct_of_cents, resolve_dunning_policy
from .dunning import STAGES, check_due, money
from .late_fees import late_fee_summary
from .messaging import (message_channel, messaging_configured,
                        messaging_from_config, normalize_phone,
                        render_message, send_message)
from .reports import (freelancer_earnings, invoices_csv, payments_csv,
                      payout_suggestions, receivables_report)
from .stripe_links import create_payment_link, default_due_date

TEMPLATE_SOURCE = Path(__file__).resolve().parent / "email_templates"


def _amount_to_cents(raw: str) -> int:
    """Parse a user-typed money amount ("250", "19.99") into exact cents.

    Decimal parsing rounded half-up — the project's money convention
    (see late_fees.py). Float + Python's round() (banker's rounding on
    a binary approximation) silently turned e.g. 1.005 into 100 cents
    instead of 101. Raises ClickException on garbage or non-positive
    input, before anything is written.
    """
    try:
        cents = (Decimal(str(raw).strip()) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise click.ClickException(
            f"invalid amount {raw!r} — use a plain number like 250 "
            f"or 19.99")
    if cents <= 0:
        raise click.ClickException("amount must be positive")
    return int(cents)


def _validate_percent(name: str, value: float) -> float:
    """Validate a user-typed percentage (0–100 inclusive).

    `project create` writes --deposit-pct / --late-fee-pct into the stored
    project AND into the generated contract text, so an out-of-range value
    (e.g. a 500% deposit, or a negative late-fee rate) would silently
    produce nonsense money math and a nonsense legal clause. Raises
    ClickException on anything outside [0, 100], before anything is
    written.
    """
    if not 0 <= value <= 100:
        raise click.ClickException(
            f"{name} must be between 0 and 100 (got {value})")
    return value


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
@click.option("--phone", default=None,
              help="Client phone in E.164 form, e.g. +15551234567 "
                   "(for SMS/WhatsApp escalation)")
def client_add(name, email, phone):
    """Add a client."""
    if phone and not normalize_phone(phone):
        raise click.ClickException(
            f"invalid phone number {phone!r} — use E.164 form, "
            f"e.g. +15551234567")
    db = _db()
    cid = db.add_client(name, email, normalize_phone(phone) if phone else None)
    db.close()
    click.echo(f"client #{cid}: {name} <{email or 'no email'}>"
               + (f" phone {normalize_phone(phone)}" if phone else ""))


@client.command("set-email")
@click.argument("client_id", type=int)
@click.option("--email", default=None,
              help="Email address (for dunning); omit (or pass empty) "
                   "to clear it")
def client_set_email(client_id, email):
    """Set or clear a client's email address (dunning)."""
    db = _db()
    if not db.get_client(client_id):
        db.close()
        raise click.ClickException(f"no client #{client_id}")
    normalized = email.strip() if email and email.strip() else None
    db.set_client_email(client_id, normalized)
    db.close()
    click.echo(f"client #{client_id}: email "
               f"{normalized or '(cleared)'}")


@client.command("set-phone")
@click.argument("client_id", type=int)
@click.option("--phone", default=None,
              help="Phone in E.164 form; omit (or pass empty) to clear it")
def client_set_phone(client_id, phone):
    """Set or clear a client's phone number (SMS/WhatsApp escalation)."""
    db = _db()
    if not db.get_client(client_id):
        db.close()
        raise click.ClickException(f"no client #{client_id}")
    normalized = normalize_phone(phone) if phone else None
    if phone and not normalized:
        db.close()
        raise click.ClickException(
            f"invalid phone number {phone!r} — use E.164 form, "
            f"e.g. +15551234567")
    db.set_client_phone(client_id, normalized)
    db.close()
    click.echo(f"client #{client_id}: phone "
               f"{normalized or '(cleared)'}")


def _format_policy(policy: dict) -> str:
    return ", ".join(
        f"{s}={'off' if policy[s] is None else policy[s]}"
        for s in STAGES)


@client.command("set-dunning-policy")
@click.argument("client_id", type=int)
@click.option("--day1", type=int, default=None,
              help="Days overdue before the polite nudge fires "
                   "(0 disables the stage)")
@click.option("--day7", type=int, default=None,
              help="Days overdue before the firm reminder fires "
                   "(0 disables the stage)")
@click.option("--day15", type=int, default=None,
              help="Days overdue before the formal notice fires "
                   "(0 disables the stage)")
@click.option("--reset", is_flag=True,
              help="Clear custom thresholds back to the global defaults")
def client_set_dunning_policy(client_id, day1, day7, day15, reset):
    """Set a client's dunning stage thresholds (agency mode).

    Each --dayN takes the days-overdue threshold for that stage; 0
    disables the stage for this client; omit a flag to leave it unchanged.
    Thresholds must keep escalation order (day1 -> day7 -> day15).
    """
    db = _db()
    if not db.get_client(client_id):
        db.close()
        raise click.ClickException(f"no client #{client_id}")
    if reset:
        if any(v is not None for v in (day1, day7, day15)):
            db.close()
            raise click.ClickException(
                "--reset cannot be combined with --day1/--day7/--day15")
        policy = db.reset_client_dunning_policy(client_id)
    else:
        if all(v is None for v in (day1, day7, day15)):
            db.close()
            raise click.ClickException(
                "nothing to change — pass --day1, --day7, --day15, or --reset")
        try:
            policy = db.set_client_dunning_policy(
                client_id, day1=day1, day7=day7, day15=day15)
        except ValueError as e:
            db.close()
            raise click.ClickException(str(e))
    db.close()
    click.echo(f"client #{client_id}: dunning policy "
               f"{_format_policy(policy)}")


@client.command("list")
def client_list():
    """List clients."""
    db = _db()
    rows = db.list_clients()
    db.close()
    for r in rows:
        policy = resolve_dunning_policy(r)
        overrides = [
            f"{s}={'off' if policy[s] is None else policy[s]}"
            for s in STAGES
            if r["dunning_" + s] is not None
        ]
        marker = f"  [dunning {' '.join(overrides)}]" if overrides else ""
        click.echo(f"#{r['id']:>3}  {r['name']:<30} {r['email'] or '':<28} "
                   f"{r['phone'] or ''}{marker}")


# -- freelancer (agency mode) -------------------------------------------
@cli.group()
def freelancer():
    """Manage freelancers (agency roster)."""


def _fmt_split(pct: float) -> str:
    """Render a split percentage without a dangling .0 (70, not 70.0)."""
    return f"{pct:g}%"


@freelancer.command("add")
@click.option("--name", required=True, help="Freelancer name")
@click.option("--email", default=None, help="Freelancer email")
@click.option("--phone", default=None,
              help="Freelancer phone in E.164 form, e.g. +15551234567")
@click.option("--split-pct", "split_pct", type=float, default=None,
              help="Revenue-split percentage of collected earnings "
                   "(0-100); omit for no split")
def freelancer_add(name, email, phone, split_pct):
    """Add a freelancer to the agency roster."""
    if phone and not normalize_phone(phone):
        raise click.ClickException(
            f"invalid phone number {phone!r} — use E.164 form, "
            f"e.g. +15551234567")
    if split_pct is not None:
        _validate_percent("--split-pct", split_pct)
    db = _db()
    try:
        fid = db.add_freelancer(
            name, email, normalize_phone(phone) if phone else None,
            split_pct=split_pct)
    except ValueError as e:
        db.close()
        raise click.ClickException(str(e))
    db.close()
    click.echo(f"freelancer #{fid}: {name} <{email or 'no email'}>"
               + (f" phone {normalize_phone(phone)}" if phone else "")
               + (f" split {_fmt_split(split_pct)}"
                  if split_pct is not None else ""))


@freelancer.command("set-email")
@click.argument("freelancer_id", type=int)
@click.option("--email", default=None,
              help="Email address; omit (or pass empty) to clear it")
def freelancer_set_email(freelancer_id, email):
    """Set or clear a freelancer's email address."""
    db = _db()
    if not db.get_freelancer(freelancer_id):
        db.close()
        raise click.ClickException(f"no freelancer #{freelancer_id}")
    normalized = email.strip() if email and email.strip() else None
    db.set_freelancer_email(freelancer_id, normalized)
    db.close()
    click.echo(f"freelancer #{freelancer_id}: email "
               f"{normalized or '(cleared)'}")


@freelancer.command("set-phone")
@click.argument("freelancer_id", type=int)
@click.option("--phone", default=None,
              help="Phone in E.164 form; omit (or pass empty) to clear it")
def freelancer_set_phone(freelancer_id, phone):
    """Set or clear a freelancer's phone number."""
    db = _db()
    if not db.get_freelancer(freelancer_id):
        db.close()
        raise click.ClickException(f"no freelancer #{freelancer_id}")
    normalized = normalize_phone(phone) if phone else None
    if phone and not normalized:
        db.close()
        raise click.ClickException(
            f"invalid phone number {phone!r} — use E.164 form, "
            f"e.g. +15551234567")
    db.set_freelancer_phone(freelancer_id, normalized)
    db.close()
    click.echo(f"freelancer #{freelancer_id}: phone "
               f"{normalized or '(cleared)'}")


@freelancer.command("set-split")
@click.argument("freelancer_id", type=int)
@click.option("--split-pct", "split_pct", type=float, required=True,
              help="Revenue-split percentage of collected earnings "
                   "(0-100)")
def freelancer_set_split(freelancer_id, split_pct):
    """Set a freelancer's revenue-split percentage of collected earnings.

    The split feeds `freelancer show`: each currency line reports the
    freelancer's expected share of what was collected and what is still
    due to them after recorded payouts. Bookkeeping only — it does not
    change the payout guard, which still refuses money the agency has
    not collected.
    """
    _validate_percent("--split-pct", split_pct)
    db = _db()
    try:
        db.set_freelancer_split(freelancer_id, split_pct)
    except ValueError as e:
        db.close()
        raise click.ClickException(str(e))
    db.close()
    click.echo(f"freelancer #{freelancer_id}: split "
               f"{_fmt_split(split_pct)}")


@freelancer.command("list")
def freelancer_list():
    """List freelancers."""
    db = _db()
    rows = db.list_freelancers()
    db.close()
    for r in rows:
        split = f" {_fmt_split(r['split_pct'])}" \
            if r["split_pct"] is not None else ""
        click.echo(f"#{r['id']:>3}  {r['name']:<30} {r['email'] or '':<28} "
                   f"{r['phone'] or ''}{split}")


@freelancer.command("show")
@click.argument("freelancer_id", type=int)
def freelancer_show(freelancer_id):
    """Show a freelancer: contact details, earnings, payouts, invoices.

    Earnings are per currency: billed (attributed invoices in a real
    state — drafts and voids excluded), collected (payment ledger),
    outstanding — plus paid out and still owed from the payout ledger.
    When a revenue split is set (`--split-pct` / `freelancer set-split`),
    each currency line also shows the freelancer's expected share of
    what was collected and what is still due to them after payouts.
    The attributed invoice lines and the recorded payouts follow.
    Read-only.
    """
    db = _db()
    try:
        rows = freelancer_earnings(db, freelancer_id)
    except ValueError as e:
        db.close()
        raise click.ClickException(str(e))
    f = db.get_freelancer(freelancer_id)
    attributed = [dict(r) for r in db.list_invoices()
                  if r["freelancer_id"] == freelancer_id
                  and r["status"] not in ("draft", "void")]
    for inv in attributed:
        inv["paid_cents"] = db.paid_cents(inv["id"])
        inv["outstanding_cents"] = (inv["amount_cents"]
                                   - inv["paid_cents"])
    payouts = db.list_payouts(freelancer_id)
    db.close()

    def _money(cents):
        return f"{cents / 100:,.2f}"

    click.echo(f"freelancer #{f['id']}: {f['name']}")
    if f["email"]:
        click.echo(f"  email: {f['email']}")
    if f["phone"]:
        click.echo(f"  phone: {f['phone']}")
    if f["split_pct"] is not None:
        click.echo(f"  split: {_fmt_split(f['split_pct'])} of collected "
                   f"earnings")
    for r in rows:
        cur = r["currency"] or "(no billable invoices)"
        share = ""
        if r["currency"] and r["expected_payout_cents"] is not None:
            share = (f", share ({_fmt_split(r['split_pct'])}): "
                     f"{_money(r['expected_payout_cents'])}, still due "
                     f"to freelancer: "
                     f"{_money(r['still_due_to_freelancer_cents'])}")
        click.echo(f"  {cur}: billed {_money(r['invoiced_cents'])}, "
                   f"collected {_money(r['collected_cents'])}, "
                   f"outstanding {_money(r['outstanding_cents'])} "
                   f"({r['invoice_count']} invoices)"
                   + (f", paid out {_money(r['paid_out_cents'])}, "
                      f"still owed {_money(r['still_owed_cents'])}"
                      if r["currency"] else "")
                   + share)
    if attributed:
        click.echo("  invoices:")
        for inv in sorted(attributed, key=lambda i: i["id"]):
            click.echo(
                f"    #{inv['id']:<4} {inv['client_name']:<22} "
                f"{inv['currency']} {_money(inv['amount_cents']):>12}  "
                f"collected {_money(inv['paid_cents']):>12}  "
                f"outstanding {_money(inv['outstanding_cents']):>12}  "
                f"{inv['status']}")
    if payouts:
        click.echo("  payouts:")
        for p in payouts:
            detail = f"{p['currency']} {_money(p['amount_cents']):>12}  " \
                     f"{p['paid_at'][:10]}"
            if p["method"]:
                detail += f"  {p['method']}"
            if p["note"]:
                detail += f"  \"{p['note']}\""
            click.echo(f"    #{p['id']:<4} {detail}")


# -- payout (agency mode) ----------------------------------------------
@cli.group()
def payout():
    """Record and list payouts to freelancers (agency roster)."""


@payout.command("record")
@click.option("--freelancer", "freelancer_id", type=int, required=True,
              help="Freelancer id (see `freelancer list`)")
@click.option("--amount", required=True,
              help="Amount in currency units, e.g. 250.50")
@click.option("--currency", default="USD",
              help="Currency code; matched against the freelancer's "
                   "collected invoices")
@click.option("--paid-at", "paid_at", default=None,
              help="Payout date YYYY-MM-DD (default: now)")
@click.option("--method", default=None,
              help="How it was paid, e.g. 'bank transfer'")
@click.option("--note", default=None, help="Optional memo")
def payout_record(freelancer_id, amount, currency, paid_at, method, note):
    """Record a payout to a freelancer.

    Bookkeeping only — nothing is transferred. The amount is refused
    when it exceeds what the agency has collected for that freelancer
    in that currency (collected from the payment ledger minus payouts
    already recorded); you cannot pay out money you have not received.
    """
    amount_cents = _amount_to_cents(amount)
    db = _db()
    try:
        rec = db.record_payout(freelancer_id, amount_cents,
                               currency=currency, paid_at=paid_at,
                               method=method, note=note)
    except ValueError as e:
        db.close()
        raise click.ClickException(str(e))
    db.close()
    click.echo(f"payout #{rec['id']}: freelancer #{freelancer_id} "
               f"{rec['currency']} {rec['amount_cents'] / 100:,.2f} "
               f"on {rec['paid_at'][:10]}"
               + (f"  ({rec['method']})" if rec["method"] else ""))


@payout.command("list")
@click.option("--freelancer", "freelancer_id", type=int, default=None,
              help="Only show payouts for this freelancer")
def payout_list(freelancer_id):
    """List recorded payouts, newest first."""
    db = _db()
    if freelancer_id is not None and not db.get_freelancer(freelancer_id):
        db.close()
        raise click.ClickException(f"no freelancer #{freelancer_id}")
    rows = db.list_payouts(freelancer_id)
    db.close()
    for p in rows:
        click.echo(f"#{p['id']:>3}  {p['freelancer_name']:<25} "
                   f"{p['currency']} {p['amount_cents'] / 100:>12,.2f}  "
                   f"{p['paid_at'][:10]}"
                   f"{('  ' + p['method']) if p['method'] else ''}"
                   f"{('  \"' + p['note'] + '\"') if p['note'] else ''}")

@payout.command("suggest")
@click.option("--freelancer", "freelancer_id", type=int, default=None,
              help="Only suggest payouts for this freelancer")
def payout_suggest(freelancer_id):
    """Suggest what to pay each freelancer next, per currency.

    Derived from collected earnings and the payout ledger — read-only,
    nothing is recorded or transferred. With a revenue split set, the
    suggestion is the freelancer's still-due share; without a split it
    reports what the agency still holds for them instead. Record a
    suggestion with `payout record`, which enforces its own guard.
    """
    db = _db()
    if freelancer_id is not None and not db.get_freelancer(freelancer_id):
        db.close()
        raise click.ClickException(f"no freelancer #{freelancer_id}")
    rows = payout_suggestions(db, freelancer_id)
    db.close()
    if not rows:
        click.echo("no payout suggestions")
        return
    for r in rows:
        if r["suggested_cents"] is not None:
            click.echo(f"#{r['freelancer_id']:>3}  {r['freelancer_name']:<25} "
                       f"{r['currency']} {r['suggested_cents'] / 100:>12,.2f}  "
                       f"({_fmt_split(r['split_pct'])} of "
                       f"{r['collected_cents'] / 100:,.2f} collected, "
                       f"{r['paid_out_cents'] / 100:,.2f} already paid)")
        else:
            click.echo(f"#{r['freelancer_id']:>3}  {r['freelancer_name']:<25} "
                       f"{r['currency']} {r['available_cents'] / 100:>12,.2f}  "
                       f"(available — no split set; set one with "
                       f"`freelancer set-split` for a share suggestion)")


# -- project --------------------------------------------------------------
@cli.group()
def project():
    """Manage projects (contract + terms)."""


@project.command("create")
@click.option("--client", "client_name", required=True,
              help="Client name (must already exist)")
@click.option("--title", required=True, help="Project title")
@click.option("--amount", required=True,
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
    amount_cents = _amount_to_cents(amount)
    _validate_percent("--deposit-pct", deposit_pct)
    _validate_percent("--late-fee-pct", late_fee_pct)
    if late_fee_grace_days < 0:
        raise click.ClickException(
            f"--late-fee-grace-days must be 0 or more "
            f"(got {late_fee_grace_days})")
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
               f"{money(pct_of_cents(amount_cents, deposit_pct), currency)})")
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
    """Show the acknowledgment + e-signature status of a contract.

    Two tracks lead to an acknowledged contract (see contracts.py):
    the deposit-link acknowledgment (`project ack` / `project create
    --ack`, or paying the deposit) and the formal e-signature. This
    command reports both — before 0.8.4 it looked only at the
    signature track, so an already-acknowledged project misleadingly
    reported "awaiting signature" with no mention of the ack.
    """
    db = _db()
    proj = db.get_project(project_id)
    if not proj:
        db.close()
        raise click.ClickException(f"no project #{project_id}")
    sig = db.get_signature_for_project(project_id)
    db.close()
    acked = bool(proj["contract_ack"])
    ack_at = proj["contract_ack_at"]
    if sig and sig["status"] == "signed":
        click.echo(f"project #{project_id}: SIGNED")
        click.echo(f"  signer:      {sig['signer_name']}")
        click.echo(f"  signed at:   {sig['signed_at']}")
        click.echo(f"  contract sha256: {sig['contract_hash']}")
    elif sig:
        click.echo(f"project #{project_id}: awaiting signature")
        click.echo(f"  signing link: http://127.0.0.1:8000/sign/{sig['token']}")
        if acked:
            click.echo(f"  contract acknowledged: yes — deposit-link "
                       f"acknowledgment ({ack_at}); the e-signature is "
                       f"still outstanding")
        else:
            click.echo("  contract acknowledged: no")
    elif acked:
        click.echo(f"project #{project_id}: acknowledged via deposit "
                   f"link ({ack_at}) — no e-signature requested yet; "
                   f"run `invoiceguard project sign-request "
                   f"{project_id}` for a formal e-signature")
    else:
        click.echo(f"project #{project_id}: no signature request yet — "
                   f"run `invoiceguard project sign-request {project_id}`")


# -- invoice --------------------------------------------------------------
@cli.group()
def invoice():
    """Manage invoices (Stripe payment links)."""


@invoice.command("create")
@click.option("--project", "project_id", type=int, required=True)
@click.option("--kind", type=click.Choice(["deposit", "milestone", "final"]),
              required=True)
@click.option("--amount", default=None,
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
        deposit_cents = pct_of_cents(proj["amount_cents"], proj["deposit_pct"])
        if kind == "deposit":
            amount_cents = deposit_cents
        elif kind == "final":
            amount_cents = proj["amount_cents"] - deposit_cents
        else:  # milestone
            amount_cents = proj["amount_cents"]
    else:
        amount_cents = _amount_to_cents(amount)

    due = (datetime.now(timezone.utc).date()
           ).isoformat() if due_days <= 0 else default_due_date(due_days)
    iid = db.add_invoice(project_id, kind, amount_cents, proj["currency"],
                         status="draft", due_date=due)

    cfg = load_config()
    try:
        inv = db.invoice_full(iid)
        url, link_id = create_payment_link(inv, cfg.get("stripe_secret_key", ""))
    except Exception as e:
        # Roll the draft back: a failed create must not leave an orphan
        # draft invoice behind (before 0.8.4, N failed runs left N
        # drafts). delete_invoice only removes a payment-less draft,
        # so this is safe even if state changed in between. StripeError
        # (API failure) lands here too, not just ValueError (no key).
        db.delete_invoice(iid)
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
@click.option("--freelancer", "freelancer_id", type=int, default=None,
              help="Only show invoices attributed to freelancer ID")
def invoice_list(freelancer_id):
    """List invoices."""
    db = _db()
    if freelancer_id is not None and not db.get_freelancer(freelancer_id):
        db.close()
        raise click.ClickException(f"no freelancer #{freelancer_id}")
    rows = db.list_invoices()
    db.close()
    if freelancer_id is not None:
        rows = [r for r in rows if r["freelancer_id"] == freelancer_id]
    for r in rows:
        fl = f" [{r['freelancer_name']}]" if r["freelancer_name"] else ""
        click.echo(f"#{r['id']:>3}  {r['kind']:<9} {r['status']:<14} "
                   f"{money(r['amount_cents'], r['currency']):>12}  "
                   f"{r['project_title']:<30}{fl} due {r['due_date'] or '-'}")


@invoice.command("assign")
@click.argument("invoice_id", type=int)
@click.option("--freelancer", "freelancer_id", type=int, default=None,
              help="Freelancer ID to attribute the invoice to")
@click.option("--none", "clear", is_flag=True, default=False,
              help="Clear the freelancer attribution")
def invoice_assign(invoice_id, freelancer_id, clear):
    """Attribute an invoice to a freelancer (agency bookkeeping).

    Attribution never changes dunning, payments, or invoice status —
    it only records whose work the invoice belongs to, so reports can
    break receivables down per freelancer.
    """
    if clear and freelancer_id is not None:
        raise click.ClickException(
            "--freelancer and --none are mutually exclusive")
    if not clear and freelancer_id is None:
        raise click.ClickException(
            "pass --freelancer ID to attribute, or --none to clear")
    db = _db()
    try:
        db.assign_invoice_freelancer(
            invoice_id, None if clear else freelancer_id)
    except ValueError as e:
        db.close()
        raise click.ClickException(str(e))
    db.close()
    if clear:
        click.echo(f"invoice #{invoice_id}: freelancer attribution cleared")
    else:
        click.echo(f"invoice #{invoice_id}: attributed to "
                   f"freelancer #{freelancer_id}")


@invoice.command("record-payment")
@click.argument("invoice_id", type=int)
@click.option("--amount", required=True,
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
    cents = _amount_to_cents(amount)
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
    """Void an invoice. Paid invoices cannot be voided."""
    db = _db()
    inv = db.get_invoice(invoice_id)
    if not inv:
        db.close()
        raise click.ClickException(f"no invoice #{invoice_id}")
    if inv["status"] == "paid":
        db.close()
        raise click.ClickException(
            f"invoice #{invoice_id} is already paid — a paid invoice "
            f"cannot be voided (its payments are recorded in the "
            f"ledger; refund through Stripe instead)")
    if inv["status"] == "void":
        db.close()
        raise click.ClickException(f"invoice #{invoice_id} is already void")
    db.update_invoice(invoice_id, status="void")
    db.close()
    click.echo(f"invoice #{invoice_id} voided")


@invoice.command("late-fees")
@click.argument("invoice_id", type=int)
@click.option("--as-of", "as_of", default=None,
              help="Accrual date as YYYY-MM-DD (default: today)")
def invoice_late_fees(invoice_id, as_of):
    """Show accrued late fees for an invoice (contract §3).

    Fees accrue at the project's monthly rate, compounding monthly,
    starting the day after the grace period ends: one fee per full
    monthly period elapsed since accrual started.
    """
    db = _db()
    full = db.invoice_full(invoice_id)
    db.close()
    if not full:
        raise click.ClickException(f"no invoice #{invoice_id}")
    if as_of:
        try:
            date.fromisoformat(as_of[:10])
        except ValueError:
            raise click.ClickException(
                f"bad --as-of date {as_of!r} — use YYYY-MM-DD")
    summary = late_fee_summary(full, as_of=as_of)
    cur = full["currency"]
    click.echo(f"invoice #{invoice_id}: late-fee accrual "
               f"(as of {as_of[:10] if as_of else date.today().isoformat()})")
    click.echo(f"  due date:      {full['due_date'] or '— (no due date: no fees accrue)'}")
    click.echo(f"  rate:          {full['late_fee_pct']}%/month, compounding monthly")
    click.echo(f"  grace:         {full['late_fee_grace_days']} days"
               + (f" → fees accrue from {summary['accrual_start']}"
                  if summary["accrual_start"] else ""))
    click.echo(f"  outstanding:   {money(full['outstanding_cents'], cur)}")
    click.echo(f"  months billed: {summary['months']}")
    click.echo(f"  accrued fees:  {money(summary['fees_cents'], cur)}")
    click.echo(f"  total due:     {money(summary['total_cents'], cur)}")


@invoice.command("notify")
@click.argument("invoice_id", type=int)
@click.option("--channel", type=click.Choice(["sms", "whatsapp"]),
              default=None,
              help="Channel (default: messaging.channel from config)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Render and print the message without sending it")
def invoice_notify(invoice_id, channel, dry_run):
    """Send an SMS/WhatsApp payment reminder for one invoice, now.

    Uses the same message text as the automatic day-15 escalation.
    With --dry-run it only prints the rendered message (works even
    without Twilio credentials, as long as the client has a phone).
    """
    db = _db()
    full = db.invoice_full(invoice_id)
    if not full:
        db.close()
        raise click.ClickException(f"no invoice #{invoice_id}")
    phone = normalize_phone(full.get("client_phone"))
    if not phone:
        db.close()
        raise click.ClickException(
            f"client for invoice #{invoice_id} has no valid phone number — "
            f"add one with `invoiceguard client set-phone`")
    late = late_fee_summary(full)
    context = {
        "client_name": full["client_name"],
        "project_title": full["project_title"],
        "outstanding": money(full["outstanding_cents"], full["currency"]),
        "days_overdue": max(
            0, (date.today() - date.fromisoformat(full["due_date"])).days
        ) if full["due_date"] else 0,
        "due_date": full["due_date"] or "—",
        "total_with_late_fees": money(late["total_cents"], full["currency"]),
        "pay_url": full["stripe_url"] or "(no payment link yet)",
    }
    body = render_message(context)
    cfg = load_config()
    msg_cfg = messaging_from_config(cfg)
    ch = channel or message_channel(msg_cfg)
    if dry_run:
        db.close()
        click.echo(f"[dry-run] would send {ch} to {phone}:")
        click.echo(f"  {body}")
        return
    if not messaging_configured(msg_cfg):
        db.close()
        raise click.ClickException(
            "messaging is not configured — set messaging.account_sid / "
            "auth_token / from_number in the config (or the "
            "INVOICEGUARD_TWILIO_* env vars). Tip: --dry-run previews "
            "the message without credentials.")
    try:
        reply = send_message(msg_cfg, phone, body, channel=ch)
    except (ValueError, RuntimeError) as e:
        db.close()
        raise click.ClickException(str(e))
    db.record_message(invoice_id, ch, "manual", phone,
                      provider_sid=reply.get("sid"))
    db.close()
    click.echo(f"sent {ch} to {phone} (invoice #{invoice_id})")


@invoice.command("report")
@click.option("--as-of", "as_of", default=None,
              help="Report date as YYYY-MM-DD (default: today)")
@click.option("--freelancer", "freelancer_id", type=int, default=None,
              help="Only include invoices attributed to freelancer ID")
@click.option("--csv", "csv_path", default=None, metavar="PATH",
              help="Also write one CSV row per open invoice to PATH "
                   "('-' prints the CSV instead of the text report)")
@click.option("--payments-csv", "payments_csv_path", default=None,
              metavar="PATH",
              help="Also write the full payment ledger as CSV to PATH "
                   "('-' prints it instead of the text report)")
def invoice_report(as_of, freelancer_id, csv_path, payments_csv_path):
    """Read-only receivables report: who owes what, and how late.

    Aging buckets, per-client and per-freelancer totals, and accrued
    late fees over open invoices (sent / overdue / partially-paid with
    a balance), using the same outstanding-balance definition as the
    dashboard. Never sends anything and never changes invoice state.
    """
    if csv_path == "-" and payments_csv_path == "-":
        raise click.ClickException(
            "only one CSV can be printed to stdout — "
            "give the other a file path")
    if as_of:
        try:
            date.fromisoformat(as_of[:10])
        except ValueError:
            raise click.ClickException(
                f"bad --as-of date {as_of!r} — use YYYY-MM-DD")
    db = _db()
    if freelancer_id is not None and not db.get_freelancer(freelancer_id):
        db.close()
        raise click.ClickException(f"no freelancer #{freelancer_id}")
    report = receivables_report(db, as_of=as_of, freelancer_id=freelancer_id)
    inv_csv = invoices_csv(report["rows"]) if csv_path else None
    pay_csv = payments_csv(db) if payments_csv_path else None
    db.close()

    notes = []
    stdout_csv = None
    if csv_path == "-":
        stdout_csv = inv_csv
    elif csv_path:
        Path(csv_path).write_text(inv_csv, encoding="utf-8")
        notes.append(f"wrote invoice CSV: {csv_path} "
                     f"({len(report['rows'])} rows)")
    if payments_csv_path == "-":
        stdout_csv = pay_csv
    elif payments_csv_path:
        Path(payments_csv_path).write_text(pay_csv, encoding="utf-8")
        notes.append(f"wrote payments CSV: {payments_csv_path} "
                     f"({pay_csv.count(chr(10)) - 1} rows)")
    if stdout_csv is not None:
        for n in notes:
            click.echo(n, err=True)
        click.echo(stdout_csv, nl=False)
        return

    rows = report["rows"]
    click.echo(f"receivables report (as of {report['as_of']})"
               + (f" — freelancer "
                  f"#{freelancer_id}"
                  if freelancer_id is not None else ""))
    if not rows:
        click.echo("  no open invoices — nothing outstanding")
    else:
        click.echo("")
        click.echo("aging — outstanding by days overdue:")
        multi = len(report["totals"]) > 1
        for cur in sorted(report["buckets"]):
            if multi:
                click.echo(f"  [{cur}]")
            for b in report["buckets"][cur]:
                click.echo(f"  {(b['label'] + ':'):<26} {b['count']:>3} "
                           f"invoice(s)  "
                           f"{money(b['outstanding_cents'], cur):>12}")
            t = report["totals"][cur]
            click.echo(f"  {'total:':<26} {t['count']:>3} invoice(s)  "
                       f"{money(t['outstanding_cents'], cur):>12}"
                       f"  (+ {money(t['fees_cents'], cur)} accrued late"
                       f" fees = {money(t['total_cents'], cur)} due)")
        click.echo("")
        click.echo("open invoices (oldest debt first):")
        for r in rows:
            if r["days_overdue"] is None:
                due = "no due date"
            elif r["days_overdue"] > 0:
                due = f"due {r['due_date']} ({r['days_overdue']}d overdue)"
            elif r["due_date"] > report["as_of"]:
                due = f"due {r['due_date']} (not yet due)"
            else:
                due = f"due {r['due_date']} (due today)"
            fees = (f" + {money(r['late_fees_cents'], r['currency'])} fees"
                    if r["late_fees_cents"] else "")
            click.echo(f"  #{r['id']:<4} {r['client_name']:<20} "
                       f"{r['project_title']:<28} {due:<30} "
                       f"{money(r['outstanding_cents'], r['currency']):>12}"
                       f" outstanding{fees}")
        click.echo("")
        click.echo("by client:")
        for c in report["by_client"]:
            click.echo(f"  {c['client_name']:<20} {c['count']} invoice(s)  "
                       f"{money(c['outstanding_cents'], c['currency'])}"
                       f" outstanding  "
                       f"({money(c['fees_cents'], c['currency'])} fees,"
                       f" {money(c['total_cents'], c['currency'])}"
                       f" total due)")
        if len(report["by_freelancer"]) > 1:
            click.echo("")
            click.echo("by freelancer:")
            for f in report["by_freelancer"]:
                click.echo(f"  {f['freelancer_name']:<20} {f['count']} invoice(s)  "
                           f"{money(f['outstanding_cents'], f['currency'])}"
                           f" outstanding  "
                           f"({money(f['fees_cents'], f['currency'])} fees,"
                           f" {money(f['total_cents'], f['currency'])}"
                           f" total due)")
    for n in notes:
        click.echo(n)


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
    msg_cfg = messaging_from_config(cfg)
    results = check_due(db, smtp_cfg, messaging_cfg=msg_cfg)
    db.close()
    if not results:
        click.echo("nothing due — no emails sent")
    for r in results:
        if r.get("skipped"):
            click.echo(f"skipped {r['stage'] or 'dunning'} "
                       f"(invoice #{r['invoice_id']}): "
                       f"{r['skipped']}")
            continue
        if r.get("send_error"):
            click.echo(f"failed {r['stage']} (invoice #{r['invoice_id']}): "
                       f"{r['send_error']}")
            continue
        click.echo(f"sent {r['stage']} to {r['to']} "
                   f"(invoice #{r['invoice_id']})")
        if r.get("message_to"):
            click.echo(f"  + {r['message_channel']} message to "
                       f"{r['message_to']}")
        elif r.get("message_error"):
            click.echo(f"  ! message failed: {r['message_error']}")


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
