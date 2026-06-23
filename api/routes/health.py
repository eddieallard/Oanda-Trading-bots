"""
api/routes/health.py
====================
Public health-check endpoint — no authentication required.

Interview talking points:
- A /health endpoint is a prerequisite for container orchestrators (Kubernetes
  liveness/readiness probes) and load balancers.  It must be unauthenticated
  so the orchestrator can check it without credentials.
- Returning a timestamp lets ops teams confirm the service is live *and* that
  its clock is correct (important for JWT expiry calculations).
"""

from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Service health check")
async def health_check() -> dict:
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "oanda-trading-api",
    }
