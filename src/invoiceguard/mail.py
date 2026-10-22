"""SMTP email sending (stdlib smtplib, plain text)."""

from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage

_SUBJECT_LINE = re.compile(r"^Subject:[^\n]*(\n|$)")
_BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def to_plain_text(body: str) -> str:
    """Make a rendered markdown template safe for a plain-text email.

    Two leaks fixed in 0.8.4 (both visible to real clients):
      - a leading "Subject: ..." line from the template file was sent
        as the first line of the *body* (the real subject travels in
        the Subject header; dunning.SUBJECTS is the source of truth);
      - markdown emphasis went out literally — clients received
        "**$1,250.00**" with the asterisks showing.
    Applied at send time so templates already copied into a user's
    ~/.invoiceguard/templates by an older `init` are cleaned too.
    """
    text = body.lstrip("\n")
    text = _SUBJECT_LINE.sub("", text, count=1).lstrip("\n")
    return _BOLD.sub(r"\1", text)


def send_email(smtp_cfg: dict, to_addr: str, subject: str, body: str) -> None:
    """Send one plain-text email. Raises on failure."""
    if not to_addr:
        raise ValueError("recipient address is empty")
    msg = EmailMessage()
    msg["From"] = smtp_cfg.get("from_addr") or smtp_cfg.get("username")
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(to_plain_text(body))

    host = smtp_cfg.get("host", "localhost")
    port = int(smtp_cfg.get("port", 587))
    use_tls = bool(smtp_cfg.get("use_tls", True))
    username = smtp_cfg.get("username")
    password = smtp_cfg.get("password")

    if use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as s:
            s.starttls(context=context)
            if username:
                s.login(username, password or "")
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as s:
            if username:
                s.login(username, password or "")
            s.send_message(msg)
