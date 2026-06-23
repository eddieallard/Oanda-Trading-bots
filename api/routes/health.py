"""
api/routes/health.py
====================
Public health-check endpoint — no authentication required.
Suitable for Kubernetes liveness/readiness probes and load balancer checks.
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
