"""
api/auth/dependencies.py
========================
FastAPI dependency injection for authentication.

Interview talking points:
- Depends() is FastAPI's IoC (Inversion of Control) mechanism.
  Any route that declares `_user: dict = Depends(get_current_user)` is automatically
  protected — no decorator, no manual token parsing in every handler.
- HTTPBearer extracts the "Bearer <token>" value from the Authorization header.
- Returning the full decoded payload lets downstream routes read `sub` (username),
  `role`, etc., without a second DB lookup in simple cases.
- The function can be swapped for `get_admin_user` by adding a role check,
  demonstrating how fine-grained RBAC extends naturally from this pattern.
"""

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.auth.jwt_handler import decode_token

_bearer = HTTPBearer()


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """
    FastAPI dependency — validates the Bearer token and returns the decoded payload.
    Raises HTTP 401 on any token problem.
    """
    try:
        payload = decode_token(credentials.credentials)
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type — use an access token",
            )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
