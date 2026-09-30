"""Stripe webhook handling (payment confirmations mark invoices paid).

Used by the FastAPI dashboard app (POST /webhooks/stripe) and testable
offline: stripe.Webhook.construct_event does real signature verification
with a test secret, no network needed.
"""

from __future__ import annotations

import stripe

from .config import is_placeholder
from .db import DB, now_iso


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


def handle_event(db: DB, event: dict) -> str:
    """Apply a verified Stripe event to the DB.

    Returns a short action label: 'invoice_paid', 'invoice_voided', or
    'ignored:<type>'.
    """
    etype = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}

    if etype == "checkout.session.completed":
        invoice_id = (obj.get("metadata") or {}).get("invoiceguard_invoice_id")
        if invoice_id:
            inv = db.get_invoice(int(invoice_id))
            if inv and inv["status"] != "paid":
                db.update_invoice(
                    int(invoice_id),
                    status="paid",
                    paid_at=now_iso(),
                    stripe_session_id=obj.get("id"),
                )
                return "invoice_paid"
            return "ignored:already_paid"
        return "ignored:no_invoice_metadata"

    if etype == "payment_link.expired":
        # Nothing to auto-void in v1; a human voids from the CLI/dashboard.
        return "ignored:payment_link_expired"

    return f"ignored:{etype}"
