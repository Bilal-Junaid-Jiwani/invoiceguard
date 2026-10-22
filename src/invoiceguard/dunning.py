"""3-stage dunning engine.

Stages (by days overdue):
    day1  — 1 day   — polite nudge
    day7  — 7 days  — firm reminder
    day15 — 15 days — formal notice quoting the late-fee clause

Idempotent: each stage is sent at most once per invoice (dunning_events).
Meant to run from cron (e.g. hourly or daily). Only invoices with a
status of 'sent' or 'overdue' and a due_date in the past are considered.

Per-client policies (v0.7.0, first agency-mode slice): each client may
override the stage thresholds — a positive int is a custom days-overdue
threshold, 0 disables the stage for that client, NULL keeps the global
default. Resolved by db.resolve_dunning_policy() and applied here per
invoice; stage names and idempotency are unchanged.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .config import templates_dir
from .db import DB, resolve_dunning_policy
from .late_fees import late_fee_summary
from .mail import send_email, to_plain_text
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


def stage_for(days_overdue: int, sent: set[str],
              thresholds: dict | None = None) -> str | None:
    """Pick the next escalation stage.

    The earliest stage whose threshold is met and that has not been sent
    yet (escalation order: day1 polite -> day7 firm -> day15 formal).
    Returns None when no stage is due yet, or when all due stages were
    already sent (idempotency). One email per invoice per run.

    thresholds maps stage -> days-overdue threshold, or None to disable a
    stage for this client (per-client policy); when omitted the global
    STAGE_THRESHOLDS apply.
    """
    th = STAGE_THRESHOLDS if thresholds is None else thresholds
    for s in STAGES:
        t = th.get(s)
        if t is None:
            continue  # stage disabled for this client
        if days_overdue >= t and s not in sent:
            return s
    return None


def check_due(db: DB, smtp_cfg: dict,
              templates_dir_: Path | None = None,
              messaging_cfg: dict | None = None) -> list[dict]:
    """Send due dunning emails. Returns a list of what was sent.

    Invoices whose client has no email on file are skipped — the stage
    is reported in the result dict with a "skipped" reason instead of
    crashing the whole run (email is how the stage goes out; the
    day-15 SMS/WhatsApp is only an escalation *in addition* to it).
    Nothing is recorded for a skipped invoice, so re-running check-due
    after adding the email (e.g. `invoiceguard client set-email`) sends
    the stage then.

    The same per-invoice isolation applies to *delivery* failures: if
    sending one invoice's email raises (transient SMTP error, bad
    relay, ...) the failure is reported in the result dict under
    "send_error", nothing is recorded for that invoice, and the run
    continues with the rest — one bad send no longer aborts the whole
    collections run. A failed stage is simply retried on the next run.

    Per-client dunning policies (v0.7.0): each invoice's client may
    override the stage thresholds (`invoiceguard client
    set-dunning-policy`); the resolved policy is applied per invoice, so
    one client can escalate faster while another is never auto-dunned.
    An invoice whose client has every stage disabled is reported as
    skipped (nothing recorded), making the opt-out visible in the output
    instead of silently vanishing.

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
        thresholds = resolve_dunning_policy(inv)
        stage = stage_for(days_overdue, sent, thresholds)
        if stage is None:
            if all(t is None for t in thresholds.values()):
                results.append({
                    "invoice_id": inv["id"],
                    "stage": None,
                    "to": inv["client_email"],
                    "skipped": "dunning disabled for this client "
                               "(per-client policy — change it with "
                               "`invoiceguard client set-dunning-policy`)",
                })
            continue

        if not inv.get("client_email"):
            results.append({
                "invoice_id": inv["id"],
                "stage": stage,
                "to": None,
                "skipped": "client has no email on file — add one with "
                           "`invoiceguard client set-email`",
            })
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
        # Plain-text cleanup: the template's legacy "Subject:" header
        # line must not leak into the body, and markdown ** emphasis
        # must not reach a plain-text inbox literally (send_email
        # re-applies the same cleanup as a final guard).
        body = to_plain_text(render_template(template_path, context))
        subject = SUBJECTS[stage].format(**context)

        # Per-invoice delivery isolation: a send failure for this invoice
        # (transient SMTP error, bad relay, ...) must not abort the whole
        # collections run — the rest of the batch still goes out, and the
        # failed stage is retried on the next run (nothing is recorded).
        # smtplib.SMTPException, socket and SSL errors all derive from
        # OSError; ValueError covers send_email's own recipient check.
        try:
            send_email(smtp_cfg, inv["client_email"], subject, body)
        except (OSError, ValueError) as e:
            results.append({
                "invoice_id": inv["id"],
                "stage": stage,
                "to": inv["client_email"],
                "send_error": f"email send failed: {e} — "
                              "stage not recorded, will retry next run",
            })
            continue

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
