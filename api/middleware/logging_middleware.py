"""
api/middleware/logging_middleware.py
=====================================
Structured access log for every HTTP request.
Logs method, path, status code, and duration in a machine-parseable format.
The /health path is excluded to suppress orchestrator probe noise.
"""

import logging
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("api.access")

_EXCLUDED_PATHS = {"/health"}


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "method=%s path=%s status=%s duration_ms=%.1f client=%s",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            request.client.host if request.client else "unknown",
        )
        return response
