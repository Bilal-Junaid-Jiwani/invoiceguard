"""Stripe webhook handling (payment confirmations are recorded in the payment
ledger; a payment that clears the balance marks the invoice paid).

This module is the standalone, embeddable handler (verify + apply);
the FastAPI dashboard app (POST /webhooks/stripe) applies the same
rules in its own handler in web/main.py. Testable offline:
stripe.Webhook.construct_event does real signature verification
with a test secret, no network needed.
"""

from __future__ import annotations

import stripe

from .config import is_placeholder
from .db import DB


def verify_event(payload: bytes, sig_header: str, webhook_secret: str) -> dict:
    """Verify the Stripe signature and return the event as a dict.

    Raises ValueError if the secret is not configured, and
    stripe.error.SignatureVerificationError if verification fails.
    """
    if is_placeholder(webhook_secret):
        raise ValueError(
            "Stripe webhook secret is not configured. Set "
            "stripe_webhook_secret in ~/.invoiceguard/config.yaml or export "
            "INVOICEGUARD_STRIPE_WEBHOOK_SECRET."
        )
    event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    return event


def _as_plain_dict(event):
    """Normalize a Stripe event to a plain dict.

    verify_event() returns whatever stripe.Webhook.construct_event
    returns: a StripeObject Event under stripe>=8. StripeObject is not
    a dict and has no .get(), so handlers written against dict access
    break on real events (they only ever saw dicts in tests). Its
    .to_dict() recursively converts nested objects (session, metadata)
    to plain dicts. Plain dicts pass through unchanged.
    """
    if isinstance(event, dict):
        return event
    to_dict = getattr(event, "to_dict", None)
    if callable(to_dict):
        converted = to_dict()
        if isinstance(converted, dict):
            return converted
    return {}


def handle_event(db: DB, event: dict) -> str:
    """Apply a verified Stripe event to the DB.

    Accepts either a plain dict or the StripeObject Event returned by
    verify_event() (normalized via _as_plain_dict).

    Idempotent on event id: a retried delivery of an already-processed
    event is ignored ("ignored:duplicate_event"), so a replayed
    partial-payment event can never double-record a payment and flip
    an invoice to fully-paid early. Distinct events on the same invoice
    are still processed.

    Returns a short action label: 'invoice_paid', 'invoice_voided', or
    'ignored:<type>'.
    """
    event = _as_plain_dict(event)
    event_id = event.get("id")
    if event_id and db.webhook_event_seen(event_id):
        return "ignored:duplicate_event"
    invoice_id = _invoice_id_from(event)
    action = _apply_event(db, event)
    if event_id:
        db.mark_webhook_event_processed(
            event_id, event.get("type", ""), invoice_id
        )
    return action


def _invoice_id_from(event: dict) -> int | None:
    obj = (event.get("data") or {}).get("object") or {}
    raw = (obj.get("metadata") or {}).get("invoiceguard_invoice_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


# Session payment states in which the money is actually ours. A
# checkout.session.completed with payment_status="unpaid" means a
# delayed method (ACH debit, bank transfer, ...) has only *started*;
# Stripe follows it with checkout.session.async_payment_succeeded once
# the funds clear. A missing payment_status (older payloads/fixtures)
# is treated as paid for backward compatibility.
_PAYABLE_STATUSES = (None, "paid", "no_payment_required")


def _apply_event(db: DB, event: dict) -> str:
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if etype in ("checkout.session.completed",
                 "checkout.session.async_payment_succeeded"):
        if obj.get("payment_status") not in _PAYABLE_STATUSES:
            # Delayed payment still pending — record nothing. The
            # async_payment_succeeded event applies the payment later.
            return "ignored:payment_unpaid"
        invoice_id = (obj.get("metadata") or {}).get("invoiceguard_invoice_id")
        if invoice_id:
            inv = db.get_invoice(int(invoice_id))
            if inv and inv["status"] != "paid":
                session_amount = obj.get("amount_total")
                amount_cents = (
                    int(session_amount)
                    if session_amount is not None
                    else db.outstanding_cents(int(invoice_id))
                )
                amount_cents = min(amount_cents, db.outstanding_cents(int(invoice_id)))
                db.record_payment(
                    int(invoice_id),
                    amount_cents,
                    method="stripe",
                    note=f"stripe session {obj.get('id')}",
                )
                db.update_invoice(
                    int(invoice_id), stripe_session_id=obj.get("id")
                )
                return "invoice_paid"
            return "ignored:already_paid"
        return "ignored:no_invoice_metadata"

    if etype == "payment_link.expired":
        # Nothing to auto-void in v1; a human voids from the CLI/dashboard.
        return "ignored:payment_link_expired"

    return f"ignored:{etype}"
