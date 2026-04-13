FROM python:3.11-slim AS base

# Security: create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 -d /app -s /sbin/nologin appuser

# Minimal system dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY server/ server/
COPY config/ config/

# Create cache directory
RUN mkdir -p /app/.cache && chown -R appuser:appuser /app

# Security: switch to non-root
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

ENV CACHE_DIR=/app/.cache
CMD ["python", "-m", "server.main"]
