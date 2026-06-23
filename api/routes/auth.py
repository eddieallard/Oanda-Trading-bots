"""
api/routes/auth.py
==================
Authentication endpoints: login and token refresh.
Returns 401 for bad credentials (unauthenticated), not 403 (unauthorised).
"""

import logging

import jwt
from fastapi import APIRouter, HTTPException, status

from api.auth.jwt_handler import create_access_token, create_refresh_token, decode_token
from api.models.auth import LoginRequest, RefreshRequest, TokenResponse
from api.services.auth_service import get_user, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/login", response_model=TokenResponse, summary="Obtain JWT token pair")
async def login(body: LoginRequest) -> TokenResponse:
    user = get_user(body.username)
    if not user or not verify_password(body.password, user["hashed_password"]):
        logger.warning("Failed login attempt — username='%s'", body.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    payload = {"sub": user["username"], "role": user["role"]}
    logger.info("Successful login — username='%s'", body.username)
    return TokenResponse(
        access_token=create_access_token(payload),
        refresh_token=create_refresh_token(payload),
    )


@router.post("/refresh", response_model=TokenResponse, summary="Refresh access token")
async def refresh_token(body: RefreshRequest) -> TokenResponse:
    try:
        payload = decode_token(body.refresh_token)
        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token type — provide a refresh token",
            )
        new_payload = {"sub": payload["sub"], "role": payload.get("role", "user")}
        return TokenResponse(
            access_token=create_access_token(new_payload),
            refresh_token=create_refresh_token(new_payload),
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired — please log in again",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )
