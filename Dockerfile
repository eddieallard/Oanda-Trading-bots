# ============================================================
# Dockerfile
# ============================================================
# Interview talking points:
# - Multi-stage builds are the production standard.  Stage 1 (builder)
#   installs dependencies into a venv.  Stage 2 (runtime) copies only
#   that venv — keeping the final image lean and free of build toolchain.
# - python:3.13-slim is used instead of the full image to minimise the
#   attack surface (fewer OS packages = fewer CVEs).
# - Running as a non-root user (appuser) is a security best practice.
#   If the container is compromised, the attacker has no root privileges.
# - PYTHONDONTWRITEBYTECODE / PYTHONUNBUFFERED are standard FastAPI/uvicorn
#   settings: no .pyc clutter and logs appear in real-time (important for
#   Docker log drivers like CloudWatch or Datadog).
# ============================================================

# ── Stage 1: dependency builder ──────────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /build

# System deps needed to compile bcrypt / cryptography wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only the requirements first — Docker layer cache means this layer
# is only rebuilt when requirements.txt changes, not on every code change.
COPY requirements.txt .

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Create a non-root user
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Copy the compiled venv from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application source
COPY . .

# Create the logs directory with correct ownership
RUN mkdir -p logs && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

# uvicorn with --workers 1 is correct inside a container — horizontal scaling
# is handled by the orchestrator (Kubernetes / ECS), not inside the container.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
