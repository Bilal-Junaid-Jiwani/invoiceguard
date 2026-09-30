"""Contract markdown generator.

v1 contract signature = the client acknowledgment checkbox: the client
acknowledges the contract by accepting (paying) the deposit link, and the
freelancer records that with `invoiceguard project ack`.
Real e-signature (typed name / drawn signature capture) is roadmap.
"""

from __future__ import annotations

from datetime import date

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

## 4. Sign-off (v1)
Client acknowledgment is recorded when the deposit payment link is paid.
Recorded in InvoiceGuard as contract_ack = {ack}.
"""


def render_contract(client_name: str, client_email: str | None, title: str,
                    amount_cents: int, currency: str, deposit_pct: float,
                    late_fee_pct: float, late_fee_grace_days: int) -> str:
    deposit_cents = round(amount_cents * deposit_pct / 100.0)
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
        ack="1 (client paid the deposit link)",
    )


def _money(cents: int, currency: str) -> str:
    if currency.upper() == "USD":
        return f"${cents / 100:,.2f}"
    return f"{cents / 100:,.2f} {currency.upper()}"
