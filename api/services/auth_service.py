"""
api/services/auth_service.py
=============================
User management and password hashing.

Interview talking points:
- bcrypt is used directly (without passlib) for password hashing.
  bcrypt is deliberately slow (work factor), making brute-force attacks expensive.
  Never use MD5/SHA-256 directly for passwords — they are too fast.
- USERS_DB is intentionally an in-memory dict to keep the project self-contained.
  In production you would replace this with a repository pattern backed by
  PostgreSQL/SQLite (via SQLAlchemy) or a cache like Redis — no route code changes.
- Structuring get_user() / create_user() as functions (not methods) makes them
  easy to mock in unit tests via unittest.mock.patch.
"""

from typing import Optional

import bcrypt

# ---------------------------------------------------------------------------
# In-memory user store
# ---------------------------------------------------------------------------
# Schema: { username: { "username": str, "hashed_password": str, "role": str } }
# Replace with a DB-backed repository in production.
USERS_DB: dict[str, dict] = {}


def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches the stored bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def get_user(username: str) -> Optional[dict]:
    """Retrieve a user record or None if not found."""
    return USERS_DB.get(username)


def create_user(username: str, password: str, role: str = "user") -> dict:
    """
    Hash *password* and persist a new user record.
    Returns the stored record (without the plaintext password).
    """
    record = {
        "username": username,
        "hashed_password": hash_password(password),
        "role": role,
    }
    USERS_DB[username] = record
    return record
