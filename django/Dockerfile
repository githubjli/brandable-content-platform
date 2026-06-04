# Multi-stage Django Dockerfile using uv for dependency installation.

# ---- Build stage ----
FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /build

# Copy dependency files first for layer caching
COPY pyproject.toml ./

# Install all dependencies into /build/.venv
RUN uv venv .venv && \
    uv pip install --python .venv/bin/python -e ".[dev]" --no-cache

# ---- Runtime stage ----
FROM python:3.12-slim AS runtime

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

WORKDIR /app

# Copy virtualenv from builder
COPY --from=builder /build/.venv /app/.venv

# Copy application code
COPY . /app/

# Ensure venv is on PATH
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.local

RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "30", "--access-logfile", "-"]
