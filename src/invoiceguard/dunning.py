"""3-stage dunning engine.

Stages (by days overdue):
    day1  — 1 day   — polite nudge
    day7  — 7 days  — firm reminder
    day15 — 15 days — formal notice quoting the late-fee clause

Idempotent: each stage is sent at most once per invoice (dunning_events).
Meant to run from cron (e.g. hourly or daily). Only invoices with a
status of 'sent' or 'overdue' and a due_date in the past are considered.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .config import templates_dir
from .db import DB
from .late_fees import late_fee_summary
from .mail import send_email
from .messaging import (message_channel, messaging_configured, render_message,
                        send_message)

# Stages that also escalate by SMS/WhatsApp when messaging is configured
# and the client has a phone number on file.
MESSAGE_STAGES = ("day15",)

STAGES = ("day1", "day7", "day15")
# Stage fires when days_overdue >= threshold and the stage is next in line.
STAGE_THRESHOLDS = {"day1": 1, "day7": 7, "day15": 15}

SUBJECTS = {
    "day1": "Quick nudge: invoice for {project_title} is due",
    "day7": "Reminder: invoice for {project_title} is 7 days overdue",
    "day15": "Final notice: {project_title} invoice {days_overdue} days overdue",
}


def money(cents: int, currency: str) -> str:
    if currency.upper() == "USD":
        return f"${cents / 100:,.2f}"
    return f"{cents / 100:,.2f} {currency.upper()}"


def render_template(path: Path, context: dict) -> str:
    """Render a markdown template with {var} placeholders (stdlib only)."""
    text = path.read_text(encoding="utf-8")
    try:
        return text.format(**context)
    except KeyError as e:
        raise ValueError(
            f"template {path.name} references unknown variable {e}"
        ) from e


def stage_for(days_overdue: int, sent: set[str]) -> str | None:
    """Pick the next escalation stage.

    The earliest stage whose threshold is met and that has not been sent
    yet (escalation order: day1 polite -> day7 firm -> day15 formal).
    Returns None when no stage is due yet, or when all due stages were
    already sent (idempotency). One email per invoice per run.
    """
    for s in STAGES:
        if days_overdue >= STAGE_THRESHOLDS[s] and s not in sent:
            return s
    return None


def check_due(db: DB, smtp_cfg: dict,
              templates_dir_: Path | None = None,
              messaging_cfg: dict | None = None) -> list[dict]:
    """Send due dunning emails. Returns a list of what was sent.

    When messaging_cfg is configured (Twilio credentials present), the
    day-15 stage additionally goes out as an SMS/WhatsApp message to
    clients with a phone number on file — recorded in message_events,
    at most once per stage per invoice. A failed message send never
    blocks the email: the failure is noted in the result dict instead.
    """
    templates = templates_dir() if templates_dir_ is None else templates_dir_
    today = date.today()
    results: list[dict] = []

    for inv in db.overdue_invoices():
        due = date.fromisoformat(inv["due_date"])
        days_overdue = (today - due).days
        sent = db.sent_stages(inv["id"])
        stage = stage_for(days_overdue, sent)
        if stage is None:
            continue

        template_path = templates / f"{stage}.md"
        if not template_path.exists():
            raise FileNotFoundError(
                f"dunning template missing: {template_path} "
                f"(re-run `invoiceguard init`)"
            )

        late = late_fee_summary(inv, as_of=today)
        context = {
            "client_name": inv["client_name"],
            "project_title": inv["project_title"],
            "amount": money(inv["amount_cents"], inv["currency"]),
            "paid": money(inv["paid_cents"], inv["currency"]),
            "outstanding": money(inv["outstanding_cents"], inv["currency"]),
            "due_date": inv["due_date"],
            "days_overdue": days_overdue,
            "late_fee_pct": inv["late_fee_pct"],
            "late_fee_due": money(late["fees_cents"], inv["currency"]),
            "total_with_late_fees": money(late["total_cents"], inv["currency"]),
            "pay_url": inv["stripe_url"] or "(no payment link yet)",
        }
        body = render_template(template_path, context)
        subject = SUBJECTS[stage].format(**context)

        send_email(smtp_cfg, inv["client_email"], subject, body)

        db.record_dunning(inv["id"], stage)
        db.update_invoice(inv["id"], status="overdue")
        result = {
            "invoice_id": inv["id"],
            "stage": stage,
            "to": inv["client_email"],
            "subject": subject,
        }

        if (stage in MESSAGE_STAGES and messaging_configured(messaging_cfg)
                and inv.get("client_phone")):
            channel = message_channel(messaging_cfg)
            if not db.message_sent(inv["id"], stage, channel):
                try:
                    reply = send_message(messaging_cfg,
                                         inv["client_phone"],
                                         render_message(context),
                                         channel=channel)
                    db.record_message(inv["id"], channel, stage,
                                      inv["client_phone"],
                                      provider_sid=reply.get("sid"))
                    result["message_channel"] = channel
                    result["message_to"] = inv["client_phone"]
                except (ValueError, RuntimeError) as e:
                    result["message_error"] = str(e)
        results.append(result)
    return results
