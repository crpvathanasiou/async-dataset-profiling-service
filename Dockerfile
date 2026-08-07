# syntax=docker/dockerfile:1.6

############################
# Builder stage
############################
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.3

WORKDIR /app
ENV PYTHONPATH=/app

# System deps needed for building wheels (some packages may compile native extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
  && rm -rf /var/lib/apt/lists/*

# Install Poetry (builder only)
RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

# Copy dependency manifests first (best practice for Docker layer caching)
COPY pyproject.toml poetry.lock* ./

# Install dependencies into an in-project virtualenv at /app/.venv
# --no-root: do not install this project as a built wheel; we just need deps + source code
RUN poetry config virtualenvs.in-project true \
  && poetry install --with dev --no-interaction --no-ansi --no-root

# Copy application code and tests
COPY app ./app
COPY tests ./tests
COPY pyrightconfig.json ./pyrightconfig.json

# Run quality gates during build (fail fast)
RUN ./.venv/bin/pytest
RUN ./.venv/bin/ruff check .
RUN ./.venv/bin/pyright

# Re-install only runtime dependencies (drop dev deps for a smaller runtime image)
RUN poetry install --only main --sync --no-interaction --no-ansi --no-root


############################
# Runtime stage
############################
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Create a non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app
ENV PYTHONPATH=/app

# Copy the virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy only the application code (not tests)
COPY app ./app

# Ensure venv is used
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Healthcheck using Python stdlib (no curl required)
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import urllib.request; import sys; \
url='http://127.0.0.1:8000/health/live'; \
sys.exit(0) if urllib.request.urlopen(url, timeout=2).status==200 else sys.exit(1)"

# Run as non-root
USER appuser

# Production flags:
# - 0.0.0.0 so Docker can expose it
# - 1 Uvicorn worker: 1 container / ECS task = 1 application process
# - proxy headers for real deployments behind a reverse proxy
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
