---
title: Installation
eyebrow: Getting started
description: Install InvoiceGuard with pip install invoiceguard, or from source. Requirements and verification.
---
## From PyPI (recommended)

```bash
pip install invoiceguard
```

Requires Python 3.10 or newer. Verify:

```bash
invoiceguard --version
# invoiceguard, version 0.1.0
```

## From source

```bash
git clone https://github.com/Bilal-Junaid-Jiwani/invoiceguard
cd invoiceguard
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .
```

## Dependencies

Installed automatically from PyPI:

| Package | Used for |
|---|---|
| `stripe>=12,<16` | payment links, webhook signature verification |
| `click>=8` | the CLI |
| `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `python-multipart>=0.0.18`, `jinja2>=3.1` | the dashboard web UI |
| `PyYAML>=6` | config file parsing |

Test-only: `pytest>=8`, `httpx>=0.27`.

## First step after install

```bash
invoiceguard init
```

This creates `~/.invoiceguard/` — a config template (`config.yaml`), an empty SQLite database, and the three dunning email templates. Then continue to the [Quickstart](quickstart.html).

## Upgrading

```bash
pip install --upgrade invoiceguard
```

Check the [Changelog](changelog.html) for what changed between versions.
