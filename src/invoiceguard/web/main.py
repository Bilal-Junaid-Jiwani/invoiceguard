"""InvoiceGuard dashboard — localhost web UI.

Run:
    uvicorn invoiceguard.web.main:app --port 8000   (or: invoiceguard dashboard)

Env:
    INVOICEGUARD_DB                    SQLite path (default ~/.invoiceguard/invoiceguard.db)
    INVOICEGUARD_STRIPE_WEBHOOK_SECRET Stripe webhook signing secret (webhook endpoint only)
    STRIPE_WEBHOOK_SECRET              legacy alias for the webhook secret
    INVOICEGUARD_DASHBOARD_TOKEN       token required on the dashboard pages
                                       (see below); config key dashboard_token
                                       is honored when the env var is unset
The config file (~/.invoiceguard/config.yaml, key stripe_webhook_secret)
is also honored when neither env var is set.

Dashboard authentication: the pages that expose client/invoice data
(GET / and GET /invoices/{id}) require the dashboard token when one is
configured — as an `Authorization: Bearer <token>` header, or via
`?token=<token>` in the URL (which then sets a cookie so in-dashboard
links keep working). When NO token is configured the dashboard is
UNAUTHENTICATED and startup logs/prints a loud warning saying so;
only run it that way on a trusted localhost. The webhook endpoint
(Stripe-signature verified), the /sign/{token} pages (the token in
the URL is the capability) and /healthz are never token-gated.

Routes:
    GET  /                 invoice list + totals + status filter
                           (payout timing: ?as_of=YYYY-MM-DD projects it)
    GET  /invoices/{id}    invoice detail + timeline (same ?as_of=)
    GET  /sign/{token}     one-time contract signing page (e-signature)
    POST /sign/{token}     record the client's signature
    POST /webhooks/stripe  Stripe checkout.session.completed → mark paid
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
import html
from contextlib import asynccontextmanager
from datetime import date

import stripe
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from ..config import is_placeholder, load_config
from ..db import DB as CoreDB
from ..late_fees import late_fee_summary
from ..reports import payout_schedule_timing


def webhook_secret() -> str | None:
    """Resolve the Stripe webhook signing secret.

    Priority: INVOICEGUARD_STRIPE_WEBHOOK_SECRET env var, then the
    stripe_webhook_secret key from the config file (both documented in
    the README), then the legacy bare STRIPE_WEBHOOK_SECRET env var for
    backward compatibility. Placeholders (e.g. "whsec_...") are treated
    as unconfigured. Returns None when no real secret is set.
    """
    candidates = [
        os.environ.get("INVOICEGUARD_STRIPE_WEBHOOK_SECRET"),
        (load_config().get("stripe_webhook_secret") or None),
        os.environ.get("STRIPE_WEBHOOK_SECRET"),
    ]
    for c in candidates:
        if not is_placeholder(c):
            return c.strip()
    return None


def dashboard_token() -> str | None:
    """Resolve the dashboard access token.

    Priority: INVOICEGUARD_DASHBOARD_TOKEN env var, then the
    dashboard_token key from the config file. Placeholders (e.g.
    "change-me") are treated as unconfigured. Returns None when no
    real token is set — the dashboard pages are then unauthenticated
    (see the startup warning in lifespan()).
    """
    candidates = [
        os.environ.get("INVOICEGUARD_DASHBOARD_TOKEN"),
        (load_config().get("dashboard_token") or None),
    ]
    for c in candidates:
        if not is_placeholder(c):
            return c.strip()
    return None


logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # create empty tables if the DB is fresh; never inserts data
    if not dashboard_token():
        msg = (
            "WARNING: InvoiceGuard dashboard authentication is NOT "
            "configured — anyone who can reach this app can read every "
            "client, invoice and payment record. Set "
            "INVOICEGUARD_DASHBOARD_TOKEN (or dashboard_token in "
            "~/.invoiceguard/config.yaml) to require a token on the "
            "dashboard pages, and never expose the app beyond a trusted "
            "host without one."
        )
        logger.warning(msg)
        print(msg, file=sys.stderr)
    yield


app = FastAPI(title="InvoiceGuard", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


_DASHBOARD_COOKIE = "invoiceguard_dashboard_token"


def require_dashboard_token(request: Request) -> None:
    """Gate for the pages that expose client/invoice data.

    When a dashboard token is configured (dashboard_token()), the
    request must present it — `Authorization: Bearer <token>`, the
    cookie set by an earlier ?token= visit (see the middleware below),
    or `?token=<token>` in the URL itself. When no token is configured
    the dashboard is open — lifespan() warns loudly about that at
    startup.
    """
    token = dashboard_token()
    if not token:
        return
    provided: str | None = None
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    if provided is None:
        provided = request.cookies.get(_DASHBOARD_COOKIE)
    if provided is None:
        provided = request.query_params.get("token")
    if provided is None or not hmac.compare_digest(
            provided.encode("utf-8"), token.encode("utf-8")):
        raise HTTPException(
            status_code=401,
            detail="dashboard token required — send "
                   "'Authorization: Bearer <token>' or open the page "
                   "with ?token=<token>",
            headers={"WWW-Authenticate": "Bearer"},
        )


@app.middleware("http")
async def _dashboard_token_cookie(request: Request, call_next):
    """Persist a valid ?token= presentation as an HttpOnly cookie.

    Dashboard links (status filters, invoice details) cannot carry an
    Authorization header, so after one successful ?token= visit the
    browser authenticates with the cookie instead. (This lives in
    middleware, not the auth dependency, because the dashboard routes
    return TemplateResponse objects directly, and a dependency's
    Response never merges into those.) Only successful responses to
    the dashboard pages themselves set the cookie.
    """
    response = await call_next(request)
    if (response.status_code < 400
            and (request.url.path == "/"
                 or request.url.path.startswith("/invoices/"))):
        token = dashboard_token()
        presented = request.query_params.get("token")
        if token and presented and hmac.compare_digest(
                presented.encode("utf-8"), token.encode("utf-8")):
            response.set_cookie(_DASHBOARD_COOKIE, token,
                                httponly=True, samesite="lax")
    return response


def _base_ctx() -> dict:
    return {
        "fmt": db.format_money,
        "db_path": str(db.db_path()),
        "stage_display": db.STAGE_DISPLAY,
    }


@app.get("/", response_class=HTMLResponse,
         dependencies=[Depends(require_dashboard_token)])
def index(request: Request, status: str | None = Query(default=None),
          as_of: str | None = Query(default=None)):
    if status and status not in db.STATUSES:
        raise HTTPException(status_code=404, detail="unknown status filter")
    as_of = _parse_as_of(as_of)
    ctx = _base_ctx()
    ctx.update(
        invoices=db.list_invoices(status=status),
        totals=db.totals(),
        counts=db.status_counts(),
        active=status or "all",
        statuses=db.STATUSES,
        payouts=_dashboard_payouts(as_of, status=status),
    )
    return templates.TemplateResponse(request, "index.html", ctx)


def _parse_as_of(raw: str | None) -> str | None:
    """Validate the dashboard's `?as_of=YYYY-MM-DD` projection date.

    Returns the validated ISO date string, or None when no projection
    was asked for (timing is as of today). An unparseable value is a
    clean 400, not a traceback — the same contract as the CLI's
    invalid `--as-of`.
    """
    if raw is None:
        return None
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="invalid as_of date (expected YYYY-MM-DD)")
    return raw


_STATUS_CLASS = {
    "overdue": "overdue",
    "due": "due",
    "upcoming": "upcoming",
    "settled": "settled",
    "no schedule": "no-schedule",
}


def _payout_timing_text(entry: dict) -> str:
    """The CLI's payout-timing phrase for one rollup entry.

    Same wording `freelancer list` appends to each roster line, so the
    dashboard and the CLI never disagree about a freelancer: due /
    overdue / upcoming carry the next or missed run date, with a
    currency count when more than one currency shares the status;
    settled and no schedule are bare. No entry (no billable invoices)
    is the caller's problem — this returns "" for it.
    """
    status = entry.get("payout_status")
    count = entry.get("payout_currencies") or 0
    many = f" ({count} currencies, " if count > 1 else " ("
    if status == "overdue":
        return f"payout overdue{many}missed {entry['missed_payout_date']})"
    if status == "due":
        return f"payout due{many}next payout {entry['next_payout_date']})"
    if status == "upcoming":
        return f"payout upcoming{many}next payout {entry['next_payout_date']})"
    if status == "settled":
        return "payout settled"
    if status == "no schedule":
        return "payout no schedule"
    return ""


def _dashboard_payouts(as_of: str | None = None,
                       status: str | None = None) -> dict:
    """Per-freelancer payout timing for the dashboard index (read-only).

    One row per roster freelancer who has a timing entry — the same
    `payout_schedule_timing()` rollup `payout schedule` and
    `freelancer list` use, as of `as_of` (default: today), so the
    dashboard answers "does this freelancer need paying now" without
    opening the CLI — and, with `?as_of=`, as of the day the agency
    is planning for. Freelancers with no billable invoices have no
    entry and get no row, matching the CLI views. `has_freelancers`
    is False only when the roster itself is empty, so the section
    hides on a fresh DB. `today_url` drops the projection (keeping
    the status filter) so the header can link back to today.
    """
    core = CoreDB(db.db_path())
    try:
        roster = core.list_freelancers()
        timing = payout_schedule_timing(core, as_of=as_of)
    finally:
        core.close()
    rows = []
    for r in roster:
        entry = timing.get(r["id"], {})
        text = _payout_timing_text(entry)
        if not text:
            continue
        rows.append({
            "name": r["name"],
            "status": entry.get("payout_status"),
            "status_class": _STATUS_CLASS.get(entry.get("payout_status"), ""),
            "text": text,
        })
    today_url = f"/?status={status}" if status else "/"
    return {"has_freelancers": bool(roster), "rows": rows,
            "as_of": as_of, "today_url": today_url}


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse,
         dependencies=[Depends(require_dashboard_token)])
def invoice_detail(request: Request, invoice_id: int,
                   as_of: str | None = Query(default=None)):
    inv = db.get_invoice(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    as_of = _parse_as_of(as_of)
    events = db.dunning_events(invoice_id)
    invoice_payments = db.payments(invoice_id)
    ctx = _base_ctx()
    ctx.update(
        invoice=inv,
        events=events,
        payments=invoice_payments,
        late=late_fee_summary(inv),
        timeline=db.build_timeline(inv, events, invoice_payments),
        payout_timing=_invoice_payout_timing(inv, as_of),
    )
    return templates.TemplateResponse(request, "detail.html", ctx)


def _invoice_payout_timing(inv: dict,
                           as_of: str | None = None) -> dict | None:
    """The assigned freelancer's payout timing for the detail page.

    The same `payout_schedule_timing()` rollup the index Payouts
    section uses, as of `as_of` (default: today) — so the detail
    page and the index never disagree about a freelancer, on the
    same projected day. None when the invoice is unassigned or the
    freelancer has no timing entry (no billable invoices); the
    Freelancer row then renders exactly as before. Read-only.
    """
    fid = inv.get("freelancer_id")
    if not fid:
        return None
    core = CoreDB(db.db_path())
    try:
        entry = payout_schedule_timing(core, as_of=as_of).get(fid, {})
    finally:
        core.close()
    text = _payout_timing_text(entry)
    if not text:
        return None
    return {
        "text": text,
        "status_class": _STATUS_CLASS.get(entry.get("payout_status"), ""),
        "as_of": as_of,
    }


@app.get("/healthz")
def healthz():
    return {"ok": True}


# -- e-signature -----------------------------------------------------
_MD_RULES = (
    (re.compile(r"^### (.*)$"), r"<h3>\1</h3>"),
    (re.compile(r"^## (.*)$"), r"<h2>\1</h2>"),
    (re.compile(r"^# (.*)$"), r"<h1>\1</h1>"),
    (re.compile(r"^&gt; (.*)$"), r"<blockquote>\1</blockquote>"),
)


def _md_inline(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


def contract_to_html(md: str) -> str:
    """Render contract markdown to safe HTML (stdlib only).

    The contract text is author-generated, but we escape it first anyway
    and only allow a small markup subset: headings, bold, blockquotes,
    and paragraphs.
    """
    parts: list[str] = []
    para: list[str] = []
    for raw in md.splitlines():
        line = html.escape(raw).strip()
        matched = False
        for rx, repl in _MD_RULES:
            m = rx.match(line)
            if m:
                if para:
                    parts.append("<p>" + _md_inline("<br>".join(para)) + "</p>")
                    para = []
                parts.append(re.sub(rx, repl, line))
                matched = True
                break
        if not matched:
            if line:
                para.append(_md_inline(line))
            elif para:
                parts.append("<p>" + _md_inline("<br>".join(para)) + "</p>")
                para = []
    if para:
        parts.append("<p>" + _md_inline("<br>".join(para)) + "</p>")
    return "\n".join(parts)


@app.get("/sign/{token}", response_class=HTMLResponse)
def sign_page(request: Request, token: str):
    """One-time contract signing page for the client."""
    sig = db.get_signature_request(token)
    if sig is None:
        raise HTTPException(status_code=404, detail="signature request not found")
    ctx = _base_ctx()
    ctx.update(
        sig=sig,
        contract_html=contract_to_html(sig["contract_md"]),
        signed=sig["status"] == "signed",
    )
    return templates.TemplateResponse(request, "sign.html", ctx)


@app.post("/sign/{token}", response_class=HTMLResponse)
async def sign_submit(request: Request, token: str):
    form = await request.form()
    try:
        res = db.sign_contract(
            token,
            signer_name=str(form.get("signer_name", "")),
            signature_image=str(form.get("signature_image", "")),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sig = db.get_signature_request(token)
    ctx = _base_ctx()
    ctx.update(sig=sig, result=res)
    return templates.TemplateResponse(request, "signed.html", ctx)


def _metadata_dict(metadata) -> dict:
    """Normalize Stripe session metadata to a plain dict.

    stripe>=15 returns metadata as a StripeObject, which is NOT a dict
    (isinstance check fails) and has no .get() — the pre-0.8.4 handler
    tested isinstance(metadata, dict), silently dropped the invoice-id
    hint under stripe>=15, and recorded no payment at all. StripeObject
    exposes .to_dict(); plain dicts (older SDKs, fixtures) pass through.
    """
    if isinstance(metadata, dict):
        return metadata
    to_dict = getattr(metadata, "to_dict", None)
    if callable(to_dict):
        try:
            converted = to_dict()
        except Exception:
            return {}
        return converted if isinstance(converted, dict) else {}
    return {}


# Session payment states in which the money is actually ours. A
# checkout.session.completed with payment_status="unpaid" means a
# delayed method (ACH debit, bank transfer, ...) has only *started* —
# Stripe follows it with checkout.session.async_payment_succeeded once
# the funds clear, and that event is handled by the same path below.
# A missing payment_status (older payloads/fixtures) is treated as paid
# for backward compatibility.
_PAYABLE_STATUSES = (None, "paid", "no_payment_required")


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook receiver.

    Verifies the Stripe-Signature header against the signing secret
    resolved by webhook_secret() (INVOICEGUARD_STRIPE_WEBHOOK_SECRET env,
    config file, or legacy STRIPE_WEBHOOK_SECRET env). On
    checkout.session.completed with the payment actually settled
    (payment_status paid / no_payment_required / absent), records the
    payment in the ledger and marks the matching invoice paid (or
    partially-paid when the session amount is less than the
    outstanding balance). A completed session whose payment is still
    unpaid (delayed methods) records nothing; the follow-up
    checkout.session.async_payment_succeeded event marks it paid.
    Idempotent on the Stripe event id (and, in mark_invoice_paid, on
    the checkout session id): redeliveries are acknowledged without
    re-applying. Returns 400 on bad signature, 500 when no secret is
    configured.
    """
    secret = webhook_secret()
    if not secret:
        raise HTTPException(
            status_code=500,
            detail="Stripe webhook secret is not configured — set "
            "stripe_webhook_secret in ~/.invoiceguard/config.yaml or "
            "export INVOICEGUARD_STRIPE_WEBHOOK_SECRET",
        )
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="invalid signature")

    # Idempotency on event id: Stripe delivers at least once, so a
    # redelivery of an event we already processed is acknowledged
    # WITHOUT re-applying it — before v0.41.0 a redelivered
    # partial-payment event recorded the payment again on every retry
    # until the invoice flipped to fully paid on a fraction of the
    # money. (The standalone handler in webhooks.py has always deduped
    # this way; the served endpoint now does the same.)
    event_id = getattr(event, "id", None)
    event_type = getattr(event, "type", None)
    if event_id and db.webhook_event_seen(event_id):
        return JSONResponse({
            "received": True,
            "marked_invoice_id": db.webhook_event_invoice(event_id),
            "duplicate": True,
        })

    marked: int | None = None
    # stripe>=8 returns an Event object (not a dict); use attribute access.
    if event_type in (
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    ):
        session = event.data.object
        if getattr(session, "payment_status", None) in _PAYABLE_STATUSES:
            metadata = _metadata_dict(getattr(session, "metadata", None))
            hint = None
            # Must match the key the CLI writes in create_payment_link().
            # 'invoiceguard_invoice_id' is canonical; 'invoice_id' is a
            # tolerated legacy alias.
            raw = metadata.get("invoiceguard_invoice_id") or metadata.get("invoice_id")
            try:
                hint = int(raw) if raw is not None else None
            except (TypeError, ValueError):
                hint = None
            session_amount = getattr(session, "amount_total", None)
            marked = db.mark_invoice_paid(
                stripe_session_id=getattr(session, "id", ""),
                invoice_id_hint=hint,
                amount_cents=int(session_amount) if session_amount is not None else None,
            )

    if event_id:
        db.mark_webhook_event_processed(event_id, event_type or "", marked)

    return JSONResponse({"received": True, "marked_invoice_id": marked})
