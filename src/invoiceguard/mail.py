"""SMTP email sending (stdlib smtplib, plain text)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage


def send_email(smtp_cfg: dict, to_addr: str, subject: str, body: str) -> None:
    """Send one plain-text email. Raises on failure."""
    if not to_addr:
        raise ValueError("recipient address is empty")
    msg = EmailMessage()
    msg["From"] = smtp_cfg.get("from_addr") or smtp_cfg.get("username")
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

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
