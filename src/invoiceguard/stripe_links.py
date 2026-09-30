"""Stripe payment-link creation (test-mode friendly).

Keys come ONLY from config/env — never hardcoded. In this environment the
Stripe connector is not connected, so live calls are verified with a mocked
stripe SDK asserting the exact API params (see tests/test_stripe.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import stripe

from .config import config_path, is_placeholder


def create_payment_link(invoice: dict, api_key: str) -> tuple[str, str]:
    """Create a Stripe Payment Link for the invoice.

    Returns (url, payment_link_id). Raises ValueError when the key is
    missing or still the placeholder; raises stripe.error.StripeError
    on real API failures.
    """
    if is_placeholder(api_key):
        raise ValueError(
            "Stripe secret key is not configured. Set stripe_secret_key in "
            f"{config_path()} (test key from "
            "https://dashboard.stripe.com/test/apikeys) or export "
            "INVOICEGUARD_STRIPE_SECRET_KEY."
        )
    stripe.api_key = api_key

    label = f"InvoiceGuard: {invoice['project_title']} ({invoice['kind']})"
    link = stripe.PaymentLink.create(
        line_items=[
            {
                "price_data": {
                    "currency": invoice["currency"].lower(),
                    "product_data": {"name": label},
                    "unit_amount": invoice["amount_cents"],
                },
                "quantity": 1,
            }
        ],
        metadata={
            "invoiceguard_invoice_id": str(invoice["id"]),
            "invoiceguard_project_id": str(invoice["project_id"]),
            "invoiceguard_kind": invoice["kind"],
        },
    )
    return link.url, link.id


def default_due_date(days: int = 7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
