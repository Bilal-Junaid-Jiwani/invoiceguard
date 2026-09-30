"""Contract clause rendering tests."""

from invoiceguard.contracts import render_contract


def test_late_fee_clause_present():
    md = render_contract("Acme", "a@b.c", "Site", 200_000, "USD",
                         50.0, 1.5, 15)
    assert "1.5% per month" in md
    assert "15 days" in md
    assert "client acknowledges by accepting the deposit link" in md


def test_deposit_math():
    md = render_contract("Acme", "a@b.c", "Site", 200_000, "USD",
                         50.0, 1.5, 15)
    assert "$2,000.00" in md      # total
    assert "$1,000.00" in md      # 50% deposit


def test_custom_terms_render():
    md = render_contract("Acme", None, "App", 500_00, "EUR",
                         30.0, 2.0, 30)
    assert "2.0% per month" in md
    assert "30 days" in md
    assert "30.0%" in md
    assert "EUR" in md


def test_ack_wording_honest():
    md = render_contract("Acme", "a@b.c", "Site", 100_00, "USD",
                         50.0, 1.5, 15)
    # v1 signature = acknowledgment via deposit-link payment, not e-sign
    assert "paying the deposit" in md.lower()
    assert "contract_ack = 1 (client paid the deposit link)" in md
