"""SMS / WhatsApp escalation messages (Twilio REST API, stdlib only).

The day-15 formal notice can now also go out as a short message, not just
email: freelancers' clients read texts even when they let email pile up.
Sending uses the Twilio Messages API (https://www.twilio.com/docs/messaging)
via urllib — no new dependency. Both channels share one API:

  - SMS:      From = your Twilio number,        To = client phone
  - WhatsApp: From = "whatsapp:+<your number>", To = "whatsapp:+<client>"

Credentials come only from the config file's `messaging:` section or the
environment (INVOICEGUARD_TWILIO_ACCOUNT_SID / INVOICEGUARD_TWILIO_AUTH_TOKEN
/ INVOICEGUARD_TWILIO_FROM_NUMBER) — never hardcoded, never committed.

When messaging is not configured, InvoiceGuard stays email-only: dunning
neither errors nor pretends a message was sent.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

TWILIO_API_BASE = "https://api.twilio.com/2010-04-01"

CHANNELS = ("sms", "whatsapp")

# Placeholder values from the config template count as "not configured",
# exactly like the Stripe webhook-secret resolution elsewhere.
_PLACEHOLDERS = {"AC_...", "your-auth-token", "+10000000000", ""}

MESSAGE_TEMPLATE = (
    "InvoiceGuard: Hi {client_name}, invoice for \"{project_title}\" — "
    "{outstanding} outstanding, {days_overdue} days overdue "
    "(due {due_date}). With late fees: {total_with_late_fees}. "
    "Pay here: {pay_url}"
)


def normalize_phone(raw: str | None) -> str | None:
    """Normalize a phone number to E.164-ish form ("+15551234567").

    Accepts a leading "+" or bare digits (a leading country code is
    assumed when "+" is missing). Returns None when the input cannot
    plausibly be a phone number: too short/long, or contains letters.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[\s().\-]", "", str(raw).strip())
    if not cleaned:
        return None
    if cleaned.startswith("00"):  # international prefix
        cleaned = "+" + cleaned[2:]
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    digits = cleaned[1:]
    if not digits.isdigit() or not (7 <= len(digits) <= 15):
        return None
    return cleaned


def messaging_from_config(cfg: dict) -> dict:
    """Resolve the messaging config: `messaging:` section + env overrides.

    Env always wins over the file, matching config.load_config's rules
    for every other secret.
    """
    msg = dict((cfg or {}).get("messaging") or {})
    if os.environ.get("INVOICEGUARD_TWILIO_ACCOUNT_SID"):
        msg["account_sid"] = os.environ["INVOICEGUARD_TWILIO_ACCOUNT_SID"]
    if os.environ.get("INVOICEGUARD_TWILIO_AUTH_TOKEN"):
        msg["auth_token"] = os.environ["INVOICEGUARD_TWILIO_AUTH_TOKEN"]
    if os.environ.get("INVOICEGUARD_TWILIO_FROM_NUMBER"):
        msg["from_number"] = os.environ["INVOICEGUARD_TWILIO_FROM_NUMBER"]
    return msg


def _is_set(value) -> bool:
    return bool(value) and str(value).strip() not in _PLACEHOLDERS


def messaging_configured(msg_cfg: dict | None) -> bool:
    """True when all three Twilio credentials are present and real."""
    if not msg_cfg:
        return False
    return all(_is_set(msg_cfg.get(k))
               for k in ("account_sid", "auth_token", "from_number"))


def message_channel(msg_cfg: dict | None) -> str:
    """Configured channel ("sms" default, or "whatsapp")."""
    ch = str((msg_cfg or {}).get("channel") or "sms").strip().lower()
    return ch if ch in CHANNELS else "sms"


def render_message(context: dict) -> str:
    """Render the short escalation message with the dunning context."""
    return MESSAGE_TEMPLATE.format(**context)


def _post_form(url: str, data: dict, account_sid: str,
               auth_token: str) -> dict:
    """POST form-encoded data with Basic auth; returns parsed JSON.

    Kept as its own function so tests can intercept the HTTP layer
    without any network access.
    """
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    creds = base64.b64encode(
        f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {creds}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(
            f"Twilio API error {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Twilio API unreachable: {e.reason}") from e


def send_message(msg_cfg: dict, to_phone: str, body: str,
                 channel: str | None = None) -> dict:
    """Send an SMS or WhatsApp message via Twilio. Returns the API reply.

    Raises ValueError for unusable input (unconfigured credentials,
    invalid phone) and RuntimeError for API/transport failures.
    """
    if not messaging_configured(msg_cfg):
        raise ValueError(
            "messaging is not configured — set messaging.account_sid, "
            "messaging.auth_token and messaging.from_number in the config "
            "(or the INVOICEGUARD_TWILIO_* env vars)")
    to_norm = normalize_phone(to_phone)
    if not to_norm:
        raise ValueError(f"invalid phone number: {to_phone!r}")
    from_norm = normalize_phone(msg_cfg.get("from_number"))
    if not from_norm:
        raise ValueError(
            f"invalid messaging.from_number: {msg_cfg.get('from_number')!r}")
    ch = channel or message_channel(msg_cfg)
    if ch not in CHANNELS:
        raise ValueError(f"unknown channel {ch!r} (use sms or whatsapp)")
    if ch == "whatsapp":
        to_addr, from_addr = f"whatsapp:{to_norm}", f"whatsapp:{from_norm}"
    else:
        to_addr, from_addr = to_norm, from_norm
    url = (f"{TWILIO_API_BASE}/Accounts/"
           f"{msg_cfg['account_sid']}/Messages.json")
    return _post_form(url, {"To": to_addr, "From": from_addr, "Body": body},
                      msg_cfg["account_sid"], msg_cfg["auth_token"])
