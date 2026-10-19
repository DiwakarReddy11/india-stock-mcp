# syntax=docker/dockerfile:1

# ---------- Stage 1: build the virtual environment ----------
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer is cached until pyproject.toml or uv.lock change.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

# Then our own code, installed as a regular (non-editable) package.
COPY src ./src
RUN uv sync --locked --no-dev --no-editable


# ---------- Stage 2: small runtime image ----------
FROM python:3.11-slim

# Never run as root inside the container.
RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PORT']}/health\", timeout=4)"]

CMD ["python", "-m", "india_stock_mcp.server"]
