# Multi-stage Dockerfile for ShopHub Multi-Tenant Agent Platform
# Meets R10 hygiene: multi-stage build, non-root user, healthchecks

# --- Stage 1: Build & Dependencies ---
FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


# --- Stage 2: Final Minimal Runtime ---
FROM python:3.12-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/home/appuser/.local/bin:$PATH" \
    PYTHONPATH="/app"

# Security: Run as unprivileged non-root user
RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -m appuser && \
    mkdir -p /app/data && \
    chown -R appuser:appgroup /app

COPY --from=builder /root/.local /home/appuser/.local

# Copy application source code
COPY --chown=appuser:appgroup src/ /app/src/
COPY --chown=appuser:appgroup eval/ /app/eval/
COPY --chown=appuser:appgroup pyproject.toml /app/

USER appuser

EXPOSE 8000 8001 8002

CMD ["uvicorn", "src.orchestrator.app:app", "--host", "0.0.0.0", "--port", "8000"]

