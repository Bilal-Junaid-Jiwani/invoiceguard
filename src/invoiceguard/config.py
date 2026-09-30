"""Configuration loading.

Config lives at ~/.invoiceguard/config.yaml by default; the
INVOICEGUARD_CONFIG environment variable overrides the path.
Secrets may also be supplied via environment variables:

    INVOICEGUARD_STRIPE_SECRET_KEY, INVOICEGUARD_STRIPE_WEBHOOK_SECRET,
    INVOICEGUARD_SMTP_PASSWORD

Env values always win over the YAML file — never the reverse.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

DEFAULT_DIR = Path.home() / ".invoiceguard"
DEFAULT_DB = DEFAULT_DIR / "invoiceguard.db"
DEFAULT_TEMPLATES_DIR = DEFAULT_DIR / "templates"

CONFIG_TEMPLATE = """\
# InvoiceGuard configuration.
# NEVER commit this file with real secrets (it is git-ignored upstream).

# Stripe — get test keys at https://dashboard.stripe.com/test/apikeys
stripe_secret_key: "sk_test_..."        # or env INVOICEGUARD_STRIPE_SECRET_KEY
stripe_webhook_secret: "whsec_..."       # or env INVOICEGUARD_STRIPE_WEBHOOK_SECRET

# SMTP — how dunning emails are sent
smtp:
  host: "smtp.example.com"              # e.g. smtp.gmail.com
  port: 587
  username: "you@example.com"
  password: "APP_PASSWORD"              # or env INVOICEGUARD_SMTP_PASSWORD
  from_addr: "you@example.com"
  use_tls: true

# Web-facing base URL (used to point the Stripe webhook at this app)
app:
  base_url: "http://localhost:8000"
"""


def config_path() -> Path:
    override = os.environ.get("INVOICEGUARD_CONFIG")
    if override:
        return Path(override).expanduser()
    return DEFAULT_DIR / "config.yaml"


def db_path() -> Path:
    return Path(os.environ.get("INVOICEGUARD_DB", str(DEFAULT_DB))).expanduser()


def templates_dir() -> Path:
    """Templates live next to the config file, so overrides stay together."""
    return config_path().parent / "templates"


def load_config() -> dict:
    """Load merged config: YAML file first, then env overrides."""
    cfg: dict = {}
    path = config_path()
    if path.exists():
        with open(path) as f:
            cfg = yaml.safe_load(f) or {}

    # Env overrides (never hardcode secrets in the file or the code).
    if os.environ.get("INVOICEGUARD_STRIPE_SECRET_KEY"):
        cfg["stripe_secret_key"] = os.environ["INVOICEGUARD_STRIPE_SECRET_KEY"]
    if os.environ.get("INVOICEGUARD_STRIPE_WEBHOOK_SECRET"):
        cfg["stripe_webhook_secret"] = os.environ["INVOICEGUARD_STRIPE_WEBHOOK_SECRET"]
    smtp = cfg.setdefault("smtp", {})
    if os.environ.get("INVOICEGUARD_SMTP_PASSWORD"):
        smtp["password"] = os.environ["INVOICEGUARD_SMTP_PASSWORD"]
    return cfg


def is_placeholder(value: str | None) -> bool:
    """True when a value still looks like the template placeholder."""
    if not value:
        return True
    v = value.strip()
    return v.endswith("...") or v in {"sk_test_...", "whsec_...", "APP_PASSWORD", "you@example.com"}
