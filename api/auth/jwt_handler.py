"""
api/auth/jwt_handler.py
=======================
Pure JWT utility functions — no FastAPI imports here intentionally.

Interview talking points:
- Two-token strategy: short-lived ACCESS token (30 min) + long-lived REFRESH token (7 days).
  This limits the blast radius of a stolen access token while keeping the UX smooth.
- The "type" claim inside the payload prevents a refresh token from being used as an
  access token and vice versa — a common JWT security mistake to avoid.
- Keeping this file framework-agnostic means it is independently unit-testable.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from config.settings import settings


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a short-lived access token."""
    payload = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload.update({"exp": expire, "type": "access"})
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict) -> str:
    """Create a long-lived refresh token."""
    payload = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days)
    payload.update({"exp": expire, "type": "refresh"})
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    """
    Decode and verify a JWT.  Raises jwt.ExpiredSignatureError or
    jwt.InvalidTokenError on failure — callers handle those explicitly.
    """
    return jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
