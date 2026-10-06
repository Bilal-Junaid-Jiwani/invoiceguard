"""InvoiceGuard dashboard — localhost web UI.

Run:
    uvicorn invoiceguard.web.main:app --port 8000   (or: invoiceguard dashboard)

Env:
    INVOICEGUARD_DB                    SQLite path (default ~/.invoiceguard/invoiceguard.db)
    INVOICEGUARD_STRIPE_WEBHOOK_SECRET Stripe webhook signing secret (webhook endpoint only)
    STRIPE_WEBHOOK_SECRET              legacy alias for the webhook secret
The config file (~/.invoiceguard/config.yaml, key stripe_webhook_secret)
is also honored when neither env var is set.

Routes:
    GET  /                 invoice list + totals + status filter
    GET  /invoices/{id}    invoice detail + timeline
    GET  /sign/{token}     one-time contract signing page (e-signature)
    POST /sign/{token}     record the client's signature
    POST /webhooks/stripe  Stripe checkout.session.completed → mark paid
"""
from __future__ import annotations

import json
import os
import re
import html
from contextlib import asynccontextmanager

import stripe
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db
from ..config import is_placeholder, load_config
from ..late_fees import late_fee_summary


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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # create empty tables if the DB is fresh; never inserts data
    yield


app = FastAPI(title="InvoiceGuard", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def _base_ctx() -> dict:
    return {
        "fmt": db.format_money,
        "db_path": str(db.db_path()),
        "stage_display": db.STAGE_DISPLAY,
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request, status: str | None = Query(default=None)):
    if status and status not in db.STATUSES:
        raise HTTPException(status_code=404, detail="unknown status filter")
    ctx = _base_ctx()
    ctx.update(
        invoices=db.list_invoices(status=status),
        totals=db.totals(),
        counts=db.status_counts(),
        active=status or "all",
        statuses=db.STATUSES,
    )
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail(request: Request, invoice_id: int):
    inv = db.get_invoice(invoice_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    events = db.dunning_events(invoice_id)
    invoice_payments = db.payments(invoice_id)
    ctx = _base_ctx()
    ctx.update(
        invoice=inv,
        events=events,
        payments=invoice_payments,
        late=late_fee_summary(inv),
        timeline=db.build_timeline(inv, events, invoice_payments),
    )
    return templates.TemplateResponse(request, "detail.html", ctx)


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


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook receiver.

    Verifies the Stripe-Signature header against the signing secret
    resolved by webhook_secret() (INVOICEGUARD_STRIPE_WEBHOOK_SECRET env,
    config file, or legacy STRIPE_WEBHOOK_SECRET env). On
    checkout.session.completed, records the payment in the ledger and marks the
    matching invoice paid (or partially-paid when the session amount is less
    than the outstanding balance).
    Returns 400 on bad signature, 500 when no secret is configured.
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

    marked: int | None = None
    # stripe>=8 returns an Event object (not a dict); use attribute access.
    if getattr(event, "type", None) == "checkout.session.completed":
        session = event.data.object
        metadata = getattr(session, "metadata", None) or {}
        hint = None
        if isinstance(metadata, dict):
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

    return JSONResponse({"received": True, "marked_invoice_id": marked})
