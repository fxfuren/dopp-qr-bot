# syntax=docker/dockerfile:1.7

# Build stage
FROM python:3.13-slim AS builder

# Set build environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build dependencies
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy and install Python dependencies
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Runtime stage
FROM python:3.13-slim

# Metadata
LABEL org.opencontainers.image.title="DOPP QR Bot"
LABEL org.opencontainers.image.description="Telegram bot for extracting QR codes from DOPP PDF documents"
LABEL org.opencontainers.image.version="1.0.0"
LABEL org.opencontainers.image.created="2026-05-28"

# Set runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Install runtime dependencies
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install -y --no-install-recommends \
    poppler-utils \
    tini \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash botuser && \
    mkdir -p /app /tmp/dopp_bot /app/logs && \
    chown -R botuser:botuser /app /tmp/dopp_bot

# Set working directory
WORKDIR /app

# Switch to non-root user
USER botuser

# Copy source code
COPY --chown=botuser:botuser src/ ./src/

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Use tini as init system
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run the bot
CMD ["python", "-m", "src.main"]
