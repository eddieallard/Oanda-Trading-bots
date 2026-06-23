# Oanda Trading System

A production-quality **FastAPI** backend that wraps a collection of algorithmic OANDA FX trading bots. The API provides a unified REST interface for bot lifecycle management, live account data, and trade monitoring — without modifying a single line of existing bot logic.

> Built to demonstrate professional backend engineering: async Python, JWT authentication, Docker, structured logging, dependency injection, and clean separation of concerns.

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Trading Bots](#trading-bots)
- [API Endpoints](#api-endpoints)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Quick Start](#quick-start)
- [Docker](#docker)
- [Authentication Flow](#authentication-flow)
- [Environment Variables](#environment-variables)
- [Design Decisions](#design-decisions)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                   FastAPI Backend                   │
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌────────────────┐   │
│  │  /auth   │  │  /bots   │  │ /account       │   │
│  │  routes  │  │  routes  │  │ /positions     │   │
│  └────┬─────┘  └────┬─────┘  │ /trades        │   │
│       │              │        │ /signals       │   │
│  ┌────▼─────┐  ┌────▼──────┐ └───────┬────────┘   │
│  │  Auth    │  │    Bot    │         │             │
│  │ Service  │  │  Manager  │  ┌──────▼──────┐     │
│  │ (bcrypt) │  │(subprocess│  │   OANDA     │     │
│  └──────────┘  │  pool)   │  │   Service   │     │
│                └────┬──────┘  │  (httpx)   │     │
│  ┌─────────────────────────┐  └──────┬──────┘     │
│  │   JWT Middleware + Auth │         │             │
│  │   Request Logging       │         │             │
│  └─────────────────────────┘         │             │
└─────────────────────────────────────────────────────┘
         │                              │
         ▼                              ▼
  ┌─────────────┐              ┌────────────────┐
  │  Bot Subproc│              │  OANDA v20 API │
  │  emacross   │              │  (fxtrade)     │
  │  vwap...    │              └────────────────┘
  │  bullflag   │
  │  emascalp   │
  │  fallingwedge│
  │  keylevel   │
  └─────────────┘
```

**Key principle:** The FastAPI layer is a *control plane*. The bots are the *data plane*. Each bot runs as an isolated subprocess — a crashed bot cannot affect the API or other bots.

---

## Trading Bots

| Name | Folder | Strategy |
|---|---|---|
| `emacross` | `EMACROSS/` | EMA crossover (9/20) with live pricing stream, ATR-based sizing, session-aware risk (Tokyo/London/NY) |
| `vwapfadeaway` | `VWAPFADEAWAY/` | VWAP + Support/Resistance confluence, per-instrument JSON audit logging, session margin caps |
| `bullflag` | `BULLFLAG/` | Bull flag pattern detection with trailing stop management, multi-instrument |
| `emascalp` | `EMASCALP/` | Short-timeframe EMA scalping with retry logic and pytz session awareness |
| `fallingwedge` | `FALLINGWEDGETEST/` | Falling wedge breakout with RSI confirmation, HTTP retry adapter |
| `keylevelrecursion` | `KEYLEVELRECURSION/` | Key level + wick rejection, ZoneInfo session filter, margin kill-switch |

All bots share common traits:
- Read credentials from `.env` via `python-dotenv`
- Connect to `https://api-fxtrade.oanda.com/v3`
- Implement a margin kill-switch at 90% utilisation
- Write structured logs to their own folder

---

## API Endpoints

### Public
| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Service health check — use for load balancer / orchestrator probes |
| `POST` | `/auth/login` | Obtain JWT access + refresh token pair |
| `POST` | `/auth/refresh` | Exchange a refresh token for a new token pair |

### Protected (requires `Authorization: Bearer <token>`)
| Method | Path | Description |
|---|---|---|
| `GET` | `/bots` | List all discovered bots with current status |
| `GET` | `/bots/status` | Refresh and return live subprocess status for all bots |
| `POST` | `/bots/start/{bot_name}` | Start a bot as a managed subprocess |
| `POST` | `/bots/stop/{bot_name}` | Gracefully stop a running bot (SIGTERM → SIGKILL) |
| `POST` | `/bots/restart/{bot_name}` | Stop then restart a bot |
| `GET` | `/account` | OANDA account summary (balance, NAV, margin) |
| `GET` | `/positions` | All open positions |
| `GET` | `/trades` | All open trades |
| `GET` | `/signals` | Recent entries from bot log files |

**Interactive docs available at:**
- Swagger UI → `http://localhost:8000/docs`
- ReDoc → `http://localhost:8000/redoc`

---

## Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Web framework | **FastAPI** | Async-native, automatic OpenAPI docs, Pydantic validation |
| ASGI server | **Uvicorn** | High-performance, production-grade |
| Data validation | **Pydantic v2** | Type-safe models, fast validation |
| Configuration | **pydantic-settings** | 12-Factor `.env` management with type coercion |
| Authentication | **PyJWT + bcrypt** | Industry-standard JWT + secure password hashing |
| Async HTTP | **httpx** | Non-blocking OANDA API calls (requests would block the event loop) |
| Bot isolation | **asyncio subprocess** | Process isolation — crashes don't propagate |
| Containerisation | **Docker** (multi-stage) | Lean production image with non-root user |

---

## Project Structure

```
OandaTradingSystem/
│
├── api/
│   ├── auth/
│   │   ├── jwt_handler.py          # Token generation & decoding (no FastAPI imports)
│   │   └── dependencies.py         # Depends(get_current_user) — route protection
│   ├── middleware/
│   │   └── logging_middleware.py   # Structured access log per request
│   ├── models/
│   │   ├── auth.py                 # LoginRequest, TokenResponse, RefreshRequest
│   │   ├── bot.py                  # BotInfo, BotStatus enum, BotActionResponse
│   │   └── account.py              # AccountSummary, Position, Trade, SignalEntry
│   ├── routes/
│   │   ├── health.py               # GET /health
│   │   ├── auth.py                 # POST /auth/login  /auth/refresh
│   │   ├── bots.py                 # GET/POST /bots/*
│   │   └── account.py              # GET /account /positions /trades /signals
│   └── services/
│       ├── auth_service.py         # bcrypt hashing, in-memory user store
│       ├── bot_manager.py          # Async subprocess lifecycle manager
│       └── oanda_service.py        # Async httpx OANDA REST client
│
├── config/
│   └── settings.py                 # Pydantic BaseSettings singleton
│
├── BULLFLAG/                       # ← existing bots, completely untouched
├── EMACROSS/
├── EMASCALP/
├── FALLINGWEDGETEST/
├── KEYLEVELRECURSION/
├── VWAPFADEAWAY/
│
├── logs/                           # API-level logs (bots keep their own)
├── main.py                         # App factory + lifespan context manager
├── requirements.txt
├── Dockerfile
├── .dockerignore
├── .env.example                    # Safe template — copy to .env and fill in
└── .gitignore
```

---

## Quick Start

### Prerequisites
- Python 3.13+
- An OANDA account ([practice account](https://www.oanda.com/demo-account/) is free)

### 1. Clone & set up environment

```bash
git clone https://github.com/eddieallard/Oanda-Trading-bots.git
cd Oanda-Trading-bots

python -m venv myenv
# Windows
myenv\Scripts\activate
# macOS/Linux
source myenv/bin/activate

pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in:
- `OANDA_ACCOUNT_ID` — your OANDA account ID
- `OANDA_ACCESS_TOKEN` — your OANDA API token
- `JWT_SECRET_KEY` — generate with: `python -c "import secrets; print(secrets.token_hex(32))"`
- `ADMIN_PASSWORD` — change from the default before deploying

### 3. Run the API

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000/docs** for the interactive Swagger UI.

---

## Docker

### Build and run

```bash
# Build the image
docker build -t oanda-trading-api .

# Run with environment variables from .env
docker run -p 8000:8000 --env-file .env oanda-trading-api
```

### Docker design choices

- **Multi-stage build** — Stage 1 installs dependencies; Stage 2 copies only the compiled venv. The final image has no build toolchain (smaller, fewer CVEs).
- **Non-root user** — The container runs as `appuser`. If compromised, the attacker has no root access to the host.
- **`--workers 1`** — Horizontal scaling is handled by the orchestrator (Kubernetes/ECS), not inside the container.

---

## Authentication Flow

```
Client                          API
  │                              │
  │  POST /auth/login            │
  │  { username, password }  ──► │  bcrypt.verify(password, stored_hash)
  │                              │
  │ ◄── { access_token (30m),   │  JWT signed with HS256 + secret key
  │        refresh_token (7d) }  │
  │                              │
  │  GET /bots                   │
  │  Authorization: Bearer <at> ►│  decode_token() → validate type="access"
  │ ◄── [ bot list ]             │
  │                              │
  │  POST /auth/refresh          │  (when access token expires)
  │  { refresh_token }       ──► │  validate type="refresh" → issue new pair
  │ ◄── { new access_token,     │
  │        new refresh_token }   │
```

**Security notes:**
- Passwords are hashed with **bcrypt** (work factor 12) — never stored in plaintext
- Access tokens are short-lived (30 min) to limit blast radius of theft
- Refresh tokens are long-lived (7 days) — store them securely (httpOnly cookie in browser apps)
- The `type` claim inside the JWT payload prevents a refresh token from being used as an access token

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OANDA_ACCOUNT_ID` | ✅ | — | Your OANDA account ID |
| `OANDA_ACCESS_TOKEN` | ✅ | — | Your OANDA API access token |
| `TRADING_INSTRUMENTS` | — | `""` | Comma-separated instrument list |
| `JWT_SECRET_KEY` | ✅ | — | Min 32-char random string for JWT signing |
| `JWT_ALGORITHM` | — | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | — | `30` | Access token TTL |
| `REFRESH_TOKEN_EXPIRE_DAYS` | — | `7` | Refresh token TTL |
| `ADMIN_USERNAME` | — | `admin` | Default admin username (seeded on startup) |
| `ADMIN_PASSWORD` | — | `changeme` | Default admin password — **change this** |
| `ENVIRONMENT` | — | `development` | Runtime environment label |
| `LOG_LEVEL` | — | `INFO` | Python logging level |

---

## Design Decisions

### Why subprocesses instead of importing the bots?

The bots were designed to be run directly (`python bot.py`). Importing them as modules would require refactoring each one — violating the requirement to leave existing code untouched. Subprocesses give full **process isolation**: a bot crash cannot affect the API or other bots. This is also how production systems work (systemd, Kubernetes pods, supervisor).

### Why `httpx` instead of `requests` for OANDA calls?

`requests` is synchronous. Calling it inside a FastAPI async route would **block the entire event loop**, making every other request wait. `httpx` is the async-native drop-in replacement that integrates correctly with `asyncio`.

### Why Pydantic `BaseSettings` instead of raw `os.getenv()`?

- **Validation at startup** — the app refuses to start if a required variable is missing, rather than failing silently at runtime
- **Type coercion** — `int`, `bool`, and `list` fields are parsed automatically
- **Single source of truth** — all config in one file, importable anywhere as `from config.settings import settings`

### Why two JWT tokens (access + refresh)?

A short-lived access token (30 min) limits the damage if it is stolen — it expires quickly. A long-lived refresh token (7 days) lets the user stay logged in without re-entering credentials. The `type` claim inside the payload ensures a refresh token cannot be used as an access token (a common JWT vulnerability).

---

## License

MIT
