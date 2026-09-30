"""Stripe payment-link tests — mocked stripe SDK, params asserted.

The Stripe connector is not connected in this environment, so no live call
is made or claimed. The mock asserts the exact API parameters the real
call would use.
"""

import types

import pytest
import stripe

from invoiceguard.stripe_links import create_payment_link


@pytest.fixture()
def fake_payment_link(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return types.SimpleNamespace(
            url="https://buy.stripe.com/test_fake123",
            id="plink_test_fake123",
        )

    monkeypatch.setattr(stripe.PaymentLink, "create", fake_create)
    return captured


def _invoice():
    return {
        "id": 7,
        "project_id": 3,
        "project_title": "Website redesign",
        "kind": "deposit",
        "amount_cents": 100_000,
        "currency": "USD",
    }


def test_line_items_params(fake_payment_link):
    url, link_id = create_payment_link(_invoice(), "sk_test_real_looking")
    li = fake_payment_link["line_items"]
    assert len(li) == 1
    pd = li[0]["price_data"]
    assert pd["currency"] == "usd"
    assert pd["unit_amount"] == 100_000
    assert "Website redesign" in pd["product_data"]["name"]
    assert "deposit" in pd["product_data"]["name"]
    assert li[0]["quantity"] == 1
    assert url == "https://buy.stripe.com/test_fake123"
    assert link_id == "plink_test_fake123"


def test_metadata_links_back_to_invoice(fake_payment_link):
    create_payment_link(_invoice(), "sk_test_real_looking")
    md = fake_payment_link["metadata"]
    assert md["invoiceguard_invoice_id"] == "7"
    assert md["invoiceguard_project_id"] == "3"
    assert md["invoiceguard_kind"] == "deposit"


def test_non_usd_currency_lowercased(fake_payment_link):
    inv = _invoice() | {"currency": "EUR", "amount_cents": 50_00}
    create_payment_link(inv, "sk_test_real_looking")
    assert fake_payment_link["line_items"][0]["price_data"]["currency"] == "eur"


def test_placeholder_key_rejected():
    with pytest.raises(ValueError, match="not configured"):
        create_payment_link(_invoice(), "sk_test_...")


def test_missing_key_rejected():
    with pytest.raises(ValueError, match="not configured"):
        create_payment_link(_invoice(), "")
