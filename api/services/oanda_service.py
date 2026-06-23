"""
api/services/oanda_service.py
==============================
Async OANDA REST API client used by the account/positions/trades routes.

Interview talking points:
- httpx.AsyncClient is used instead of the requests library.  'requests' is
  synchronous and would block the entire asyncio event loop during an HTTP call,
  defeating the purpose of FastAPI's async architecture.
- The client is created fresh per-call with `async with`.  For high-throughput
  production use you would create a single shared client on app startup
  (stored in app.state) and reuse it — reducing TCP handshake overhead.
- All credentials come from the settings singleton — zero hardcoded secrets.
- httpx.HTTPStatusError is raised by raise_for_status() and propagated to
  the route layer, which maps it to a 502 Bad Gateway response.
"""

import logging
from typing import Optional

import httpx

from api.models.account import AccountSummary, Position, SignalEntry, Trade
from config.settings import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api-fxtrade.oanda.com/v3"


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.oanda_access_token}",
        "Content-Type": "application/json",
        "Accept-Datetime-Format": "RFC3339",
    }


async def get_account_summary() -> AccountSummary:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{_BASE_URL}/accounts/{settings.oanda_account_id}/summary",
            headers=_auth_headers(),
        )
        r.raise_for_status()
        acc = r.json()["account"]
        return AccountSummary(
            id=acc["id"],
            balance=acc["balance"],
            currency=acc["currency"],
            nav=acc["NAV"],
            unrealized_pl=acc["unrealizedPL"],
            margin_used=acc["marginUsed"],
            margin_available=acc["marginAvailable"],
            open_trade_count=int(acc.get("openTradeCount", 0)),
            open_position_count=int(acc.get("openPositionCount", 0)),
        )


async def get_open_positions() -> list[Position]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{_BASE_URL}/accounts/{settings.oanda_account_id}/openPositions",
            headers=_auth_headers(),
        )
        r.raise_for_status()
        return [
            Position(
                instrument=p["instrument"],
                long_units=p["long"]["units"],
                long_pl=p["long"]["pl"],
                short_units=p["short"]["units"],
                short_pl=p["short"]["pl"],
                unrealized_pl=p["unrealizedPL"],
            )
            for p in r.json().get("positions", [])
        ]


async def get_open_trades() -> list[Trade]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            f"{_BASE_URL}/accounts/{settings.oanda_account_id}/openTrades",
            headers=_auth_headers(),
        )
        r.raise_for_status()
        return [
            Trade(
                id=t["id"],
                instrument=t["instrument"],
                price=t["price"],
                current_units=t["currentUnits"],
                realized_pl=t["realizedPL"],
                unrealized_pl=t["unrealizedPL"],
                open_time=t["openTime"],
                state=t["state"],
            )
            for t in r.json().get("trades", [])
        ]


async def get_recent_signals(root_dir: str, max_lines: int = 30) -> list[SignalEntry]:
    """
    Read the most recent log entries from each bot's log file and surface
    them as 'signals'.  VWAPFADEAWAY writes structured JSON; others write
    plain text — both are handled.

    Interview talking point:
    - This is a lightweight alternative to a message broker (Kafka/RabbitMQ).
      For a production system you would have bots publish to a shared queue
      and this endpoint would consume from it.  The file-based approach
      demonstrates pragmatic design: the existing bots are untouched and the
      API adds value without requiring any refactoring.
    """
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    results: list[SignalEntry] = []

    bot_logs: dict[str, Path] = {
        "emacross": Path(root_dir) / "EMACROSS" / "ema_fixedR.log",
        "vwapfadeaway_logs": Path(root_dir) / "VWAPFADEAWAY" / "logs",
        "bullflag": Path(root_dir) / "BULLFLAG" / "bullflag.log",
        "emascalp": Path(root_dir) / "EMASCALP" / "emascalp.log",
        "fallingwedge": Path(root_dir) / "FALLINGWEDGETEST" / "fallingwedge.log",
        "keylevel": Path(root_dir) / "KEYLEVELRECURSION" / "keylevel_wick_bot.log",
    }

    def _tail(path: Path, n: int) -> list[str]:
        if not path.exists():
            return []
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.readlines()[-n:]

    now_iso = datetime.now(timezone.utc).isoformat()

    for bot_name, log_path in bot_logs.items():
        if log_path.is_dir():
            # VWAPFADEAWAY: iterate per-instrument log files
            for lf in sorted(log_path.glob("*.log"))[:5]:
                instrument = lf.stem
                for raw in _tail(lf, 5):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        parsed = json.loads(raw)
                        results.append(
                            SignalEntry(
                                bot="vwapfadeaway",
                                message=parsed.get("event", raw),
                                timestamp=parsed.get("timestamp", now_iso),
                                instrument=parsed.get("instrument", instrument),
                                event=parsed.get("event"),
                            )
                        )
                    except json.JSONDecodeError:
                        results.append(
                            SignalEntry(bot="vwapfadeaway", message=raw, timestamp=now_iso)
                        )
        else:
            for raw in _tail(log_path, 10):
                raw = raw.strip()
                if raw:
                    results.append(SignalEntry(bot=bot_name, message=raw, timestamp=now_iso))

    results.sort(key=lambda s: s.timestamp, reverse=True)
    return results[:max_lines]
