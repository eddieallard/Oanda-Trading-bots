"""
main.py
=======
FastAPI application factory and entry point.
Uses the lifespan context manager for startup/shutdown (FastAPI 0.95+ pattern).
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware.logging_middleware import RequestLoggingMiddleware
from api.routes import account, auth, bots, health
from api.services.auth_service import USERS_DB, create_user
from api.services.bot_manager import BotManager
from config.settings import settings

# ---------------------------------------------------------------------------
# Logging — configure before anything else so all module-level loggers inherit
# ---------------------------------------------------------------------------
_LOGS_DIR = Path("logs")
_LOGS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_LOGS_DIR / "api.log", encoding="utf-8"),
    ],
)

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — startup + shutdown in one place
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    _logger.info("=== Oanda Trading API starting up | env=%s ===", settings.environment)

    # Seed the default admin user if the store is empty.
    if not USERS_DB:
        create_user(
            username=settings.admin_username,
            password=settings.admin_password,
            role="admin",
        )
        _logger.info("Seeded default admin user: '%s'", settings.admin_username)

    # Discover and register all bots.
    root_dir = str(Path(__file__).parent)
    app.state.bot_manager = BotManager(root_dir)
    _logger.info("Bot discovery complete — %d bots registered", len(app.state.bot_manager.list_all()))

    yield  # ← application runs here

    # ── Shutdown ─────────────────────────────────────────────────────────
    _logger.info("=== Oanda Trading API shutting down ===")
    await app.state.bot_manager.stop_all()
    _logger.info("All bots stopped — shutdown complete")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Oanda Trading API",
    description=(
        "Professional REST API for managing OANDA trading bots.\n\n"
        "Authenticate via `POST /auth/login` to receive a JWT, then click "
        "**Authorize** and paste the access token."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ── Middleware (order matters — added last is executed first) ──────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten to specific origins in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

# ── Routers ───────────────────────────────────────────────────────────────
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(bots.router)
app.include_router(account.router)
