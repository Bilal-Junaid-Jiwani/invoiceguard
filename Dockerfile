# InvoiceGuard backend — Python 3.12 slim
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install pinned deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
COPY templates/ templates/
RUN pip install --no-cache-dir .

# Local-first data lives in this volume (config, SQLite DB, templates)
VOLUME ["/root/.invoiceguard"]
EXPOSE 8000

# Default: drop into the CLI. The dashboard app runs uvicorn on 8000.
ENTRYPOINT ["invoiceguard"]
CMD ["--help"]
