# syntax=docker/dockerfile:1

# --- Build stage ---
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock README.md ./

# Install production dependencies only (no dev group)
RUN uv sync --frozen --no-dev --no-install-project

# Copy application source
COPY app/ app/

# Install the project itself
RUN uv sync --frozen --no-dev

# --- Runtime stage ---
FROM python:3.12-slim AS runtime

RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid 1000 --no-create-home appuser

WORKDIR /app

# Copy the virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Ensure the venv is on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Copy application source
COPY --from=builder /app/app/ app/

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
