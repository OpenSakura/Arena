# Use uv with Python 3.13
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim@sha256:8e0fc50658f7582f45764ecb1d43c29940351c23f1bc2c9932c186f8eed8c51c

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    gcc \
    g++ \
    tzdata \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user early
RUN useradd --create-home --shell /bin/bash app && \
    mkdir -p /app && \
    chown -R app:app /app

# Change the working directory to the `app` directory
WORKDIR /app

# Switch to app user for dependency installation
USER app

# Install dependencies
RUN --mount=type=cache,target=/home/app/.cache/uv,uid=1000,gid=1000 \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Copy only necessary files
COPY --chown=app:app app/ /app/app/
COPY --chown=app:app alembic/ /app/alembic/
COPY --chown=app:app alembic.ini /app/
COPY --chown=app:app pyproject.toml README.md uv.lock /app/

# Sync the project
RUN --mount=type=cache,target=/home/app/.cache/uv,uid=1000,gid=1000 \
    uv sync --locked

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/livez || exit 1

# Run the application
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
