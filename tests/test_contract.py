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


def test_sign_off_wording_honest():
    md = render_contract("Acme", "a@b.c", "Site", 100_00, "USD",
                         50.0, 1.5, 15)
    # sign-off is now typed/drawn e-signature with a tamper-evidence hash —
    # still honestly framed as NOT a qualified third-party e-signature
    assert "types their full name" in md
    assert "draws a signature" in md
    assert "SHA-256" in md
    assert "not a qualified third-party e-signature service" in md
    assert "contract_ack = 1" in md
