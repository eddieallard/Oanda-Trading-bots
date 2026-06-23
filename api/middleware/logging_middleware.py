"""
api/middleware/logging_middleware.py
=====================================
Structured access log for every HTTP request.

Interview talking points:
- BaseHTTPMiddleware wraps every request regardless of which route handles it —
  a clean cross-cutting concern implemented in one place (AOP-style).
- We log *after* the response so we can include the status code and duration.
  Logging before the response would miss that information.
- perf_counter() is used instead of time.time() for sub-millisecond precision.
  time.time() has platform-dependent resolution; perf_counter() is monotonic.
- The structured format (key=value pairs) is deliberately machine-parseable so
  tools like Datadog, Grafana Loki, or Splunk can ingest and query it without
  custom parsers.
- The /health path is excluded from logs to avoid noise from orchestrator probes.
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
