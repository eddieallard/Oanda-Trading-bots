"""
config/settings.py
==================
Central configuration using Pydantic BaseSettings.
All secrets are read from the environment (or .env file) — never hardcoded.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # silently ignore extra vars in .env (e.g. TRADING_INSTRUMENTS)
    )

    # ── OANDA ─────────────────────────────────────────────────────────────────
    oanda_account_id: str
    oanda_access_token: str
    trading_instruments: str = ""

    # ── JWT ───────────────────────────────────────────────────────────────────
    # Minimum 32 random chars.  Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ── Default admin seed account ────────────────────────────────────────────
    # Only used on first startup to create an initial user.
    # Change these in .env before deploying.
    admin_username: str = "admin"
    admin_password: str = "changeme"

    # ── Application ───────────────────────────────────────────────────────────
    environment: str = "development"
    log_level: str = "INFO"


# Module-level singleton — import this everywhere.
settings = Settings()
