"""Contract markdown generator.

v1/v2 contract acknowledgment: the client acknowledged by accepting (paying)
the deposit link, recorded with `invoiceguard project ack`.

v0.3.0+: real e-signature capture — the client opens a one-time signing link
(`invoiceguard project sign-request`), reads the contract, types their name
and draws a signature. The signed text is hashed (SHA-256) at signing time
and stored with the project as tamper evidence; signing flips
contract_ack = 1 (a signed contract IS the acknowledgment).

Honest scope: browser-based typed/drawn capture stored locally with a
tamper-evidence hash — NOT a qualified third-party e-signature service
(DocuSign/HelloSign). Legal weight varies by jurisdiction.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

CONTRACT_TEMPLATE = """\
# Freelance Services Agreement

**Project:** {title}
**Client:** {client_name} ({client_email})
**Date:** {date}

## 1. Scope
Work will be performed as agreed between the freelancer and the client
for the project titled "{title}".

## 2. Fee and payment
Total project fee: **{amount} {currency}**.

A deposit of **{deposit_pct}%** ({deposit_amount} {currency}) is due before
work begins. The deposit is collected through an online payment link that
also serves as the client's acknowledgment of this agreement: paying the deposit means the client has read and accepts these terms.

## 3. Late fees
If any invoice remains unpaid {grace_days} days after its due date, a late
fee of **{late_fee_pct}% per month** on the overdue balance will apply from
day {grace_days} + 1, compounding monthly until the balance is paid in full.
The client acknowledges this clause by accepting the deposit link.

> Late-fee clause: {late_fee_pct}% per month on overdue balances after {grace_days} days, client acknowledges by accepting the deposit link.

## 4. Sign-off
This agreement is signed electronically: the client types their full name
and draws a signature on the signing page. The exact text signed is hashed
(SHA-256) and stored with the project as tamper evidence, together with the
signer's name and the time of signing. Signing this agreement counts as
the client's acknowledgment (recorded in InvoiceGuard as contract_ack = 1).

> Note: this is a browser-based typed/drawn signature captured by
> InvoiceGuard, not a qualified third-party e-signature service. Legal
> weight varies by jurisdiction.
"""


def _deposit_cents(amount_cents: int, deposit_pct: float) -> int:
    """deposit_pct% of a cents amount, rounded half-up to the cent.

    The project's money convention (cli._pct_cents, late_fees.py):
    Decimal arithmetic, never float + round(). Banker's rounding on a
    binary float turned a $10.05 project at 50% into a $5.02 contract
    deposit while the CLI quoted — and the deposit invoice charged —
    $5.03.
    """
    return int((Decimal(amount_cents) * Decimal(str(deposit_pct)) / 100)
               .quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def render_contract(client_name: str, client_email: str | None, title: str,
                    amount_cents: int, currency: str, deposit_pct: float,
                    late_fee_pct: float, late_fee_grace_days: int) -> str:
    deposit_cents = _deposit_cents(amount_cents, deposit_pct)
    return CONTRACT_TEMPLATE.format(
        title=title,
        client_name=client_name,
        client_email=client_email or "n/a",
        date=date.today().isoformat(),
        amount=_money(amount_cents, currency),
        currency=currency,
        deposit_pct=deposit_pct,
        deposit_amount=_money(deposit_cents, currency),
        late_fee_pct=late_fee_pct,
        grace_days=late_fee_grace_days,
    )


def _money(cents: int, currency: str) -> str:
    if currency.upper() == "USD":
        return f"${cents / 100:,.2f}"
    return f"{cents / 100:,.2f} {currency.upper()}"
