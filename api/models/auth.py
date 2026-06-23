"""
api/models/auth.py
==================
Pydantic schemas for authentication endpoints.

Interview talking points:
- Request models validate inbound JSON and produce clear 422 errors on bad input.
- Response models act as an explicit contract — FastAPI will strip any field that
  is NOT in the response model, so internal fields never accidentally leak.
- Separating LoginRequest from TokenResponse means you can change one without
  touching the other (Open/Closed Principle).
"""

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str
