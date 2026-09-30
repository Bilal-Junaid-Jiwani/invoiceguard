"""InvoiceGuard dashboard — localhost web UI.

Run:
    uvicorn invoiceguard.web.main:app --port 8000   (or: invoiceguard dashboard)

Env:
    INVOICEGUARD_DB        SQLite path (default ~/.invoiceguard/invoiceguard.db)
    STRIPE_WEBHOOK_SECRET  Stripe webhook signing secret (webhook endpoint only)

Routes:
    GET  /                 invoice list + totals + status filter
    GET  /invoices/{id}    invoice detail + timeline
    POST /webhooks/stripe  Stripe checkout.session.completed → mark paid
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import stripe
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db

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
    ctx = _base_ctx()
    ctx.update(invoice=inv, events=events, timeline=db.build_timeline(inv, events))
    return templates.TemplateResponse(request, "detail.html", ctx)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook receiver.

    Verifies the Stripe-Signature header against STRIPE_WEBHOOK_SECRET.
    On checkout.session.completed, marks the matching invoice paid.
    Returns 400 on bad signature, 500 when no secret is configured.
    """
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(
            status_code=500, detail="STRIPE_WEBHOOK_SECRET is not configured"
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
        marked = db.mark_invoice_paid(
            stripe_session_id=getattr(session, "id", ""), invoice_id_hint=hint
        )

    return JSONResponse({"received": True, "marked_invoice_id": marked})
