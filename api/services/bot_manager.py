"""
api/services/bot_manager.py
============================
Subprocess-based bot lifecycle manager.

Interview talking points:
- Each bot runs as a child process (asyncio.create_subprocess_exec).
  Process isolation means a crashing bot cannot bring down the API — the API
  is the control plane, bots are the data plane.
- asyncio.create_subprocess_exec is non-blocking.  The API event loop stays
  fully responsive while potentially 6 bots are running concurrently.
- terminate() sends SIGTERM (graceful), followed by kill() if the process
  does not exit within 10 seconds — a standard two-stage shutdown pattern.
- BotProcess.refresh_statuses() is called before GET /bots/status so the
  status reflects reality (a bot may have crashed since it was started).
- The static BOT_REGISTRY dict is the "discovery manifest".  In a more
  advanced system this would be auto-discovered by scanning folders for a
  bot.json descriptor file, demonstrating a plugin / registry pattern.
"""

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from api.models.bot import BotInfo, BotStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bot registry — maps a stable API name to a folder + entry file.
# ---------------------------------------------------------------------------
# To add a new bot: drop its folder in the project root, add an entry here.
BOT_REGISTRY: dict[str, dict[str, str]] = {
    "emacross": {
        "folder": "EMACROSS",
        "entry": "emacross.py",
        "description": "EMA crossover with live pricing stream and ATR-based sizing",
    },
    "vwapfadeaway": {
        "folder": "VWAPFADEAWAY",
        "entry": "vwapfadeaway.py",
        "description": "VWAP + Support/Resistance confluence, session-aware margin caps",
    },
    "bullflag": {
        "folder": "BULLFLAG",
        "entry": "bullflag.py",
        "description": "Bull flag pattern detection with trailing stop management",
    },
    "emascalp": {
        "folder": "EMASCALP",
        "entry": "emascalp.py",
        "description": "Short-timeframe EMA scalping with pytz session awareness",
    },
    "fallingwedge": {
        "folder": "FALLINGWEDGETEST",
        "entry": "fallingwedge.py",
        "description": "Falling wedge breakout with RSI confirmation",
    },
    "keylevelrecursion": {
        "folder": "KEYLEVELRECURSION",
        "entry": "keylevelsrecursion.py",
        "description": "Key level + wick rejection strategy, ZoneInfo session filter",
    },
}


class BotProcess:
    """
    Wraps a single bot subprocess and tracks its runtime state.
    """

    def __init__(self, name: str, folder: str, entry: str, description: str = "") -> None:
        self.name = name
        self.folder = folder
        self.entry = entry
        self.description = description
        self.process: Optional[asyncio.subprocess.Process] = None
        self.started_at: Optional[datetime] = None
        self.status: BotStatus = BotStatus.STOPPED

    @property
    def pid(self) -> Optional[int]:
        return self.process.pid if self.process else None

    @property
    def uptime_seconds(self) -> Optional[float]:
        if self.started_at and self.status == BotStatus.RUNNING:
            return (datetime.now(timezone.utc) - self.started_at).total_seconds()
        return None

    def to_info(self) -> BotInfo:
        return BotInfo(
            name=self.name,
            folder=self.folder,
            entry_file=self.entry,
            status=self.status,
            pid=self.pid,
            started_at=self.started_at,
            uptime_seconds=self.uptime_seconds,
        )


class BotManager:
    """
    Service that owns the full lifecycle of all trading bot subprocesses.
    Instantiated once on API startup and stored on app.state.
    """

    def __init__(self, root_dir: str) -> None:
        self.root = Path(root_dir)
        self._bots: dict[str, BotProcess] = {}
        self._discover()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _discover(self) -> None:
        """Register every bot whose entry file exists on disk."""
        for name, cfg in BOT_REGISTRY.items():
            entry_path = self.root / cfg["folder"] / cfg["entry"]
            if entry_path.exists():
                self._bots[name] = BotProcess(
                    name=name,
                    folder=cfg["folder"],
                    entry=cfg["entry"],
                    description=cfg.get("description", ""),
                )
                logger.info("Bot discovered: %s → %s", name, entry_path)
            else:
                logger.warning("Bot entry not found, skipping: %s", entry_path)

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def list_all(self) -> list[BotInfo]:
        return [b.to_info() for b in self._bots.values()]

    def get(self, name: str) -> Optional[BotProcess]:
        return self._bots.get(name)

    # ------------------------------------------------------------------
    # Status refresh — call before reporting status to detect crashed bots
    # ------------------------------------------------------------------

    async def refresh_statuses(self) -> None:
        """
        Poll each running subprocess.  If the process has exited (returncode
        is set), mark it STOPPED so the API never reports a ghost as running.
        """
        for bot in self._bots.values():
            if bot.status == BotStatus.RUNNING and bot.process is not None:
                if bot.process.returncode is not None:
                    exit_code = bot.process.returncode
                    bot.status = BotStatus.STOPPED
                    bot.process = None
                    bot.started_at = None
                    logger.warning(
                        "Bot '%s' exited unexpectedly with code %s", bot.name, exit_code
                    )

    # ------------------------------------------------------------------
    # Lifecycle mutations
    # ------------------------------------------------------------------

    async def start(self, name: str) -> BotInfo:
        bot = self._bots.get(name)
        if bot is None:
            raise ValueError(f"Unknown bot: '{name}'")
        if bot.status == BotStatus.RUNNING:
            raise RuntimeError(f"Bot '{name}' is already running")

        entry_path = str(self.root / bot.folder / bot.entry)
        cwd = str(self.root / bot.folder)

        bot.process = await asyncio.create_subprocess_exec(
            sys.executable,
            entry_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        bot.started_at = datetime.now(timezone.utc)
        bot.status = BotStatus.RUNNING
        logger.info("Started bot '%s' PID=%s", name, bot.pid)
        return bot.to_info()

    async def stop(self, name: str) -> BotInfo:
        bot = self._bots.get(name)
        if bot is None:
            raise ValueError(f"Unknown bot: '{name}'")
        if bot.status != BotStatus.RUNNING or bot.process is None:
            raise RuntimeError(f"Bot '{name}' is not running")

        bot.process.terminate()
        try:
            await asyncio.wait_for(bot.process.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("Bot '%s' did not exit cleanly — sending SIGKILL", name)
            bot.process.kill()
            await bot.process.wait()

        bot.status = BotStatus.STOPPED
        bot.process = None
        bot.started_at = None
        logger.info("Stopped bot '%s'", name)
        return bot.to_info()

    async def restart(self, name: str) -> BotInfo:
        bot = self._bots.get(name)
        if bot is None:
            raise ValueError(f"Unknown bot: '{name}'")
        if bot.status == BotStatus.RUNNING:
            await self.stop(name)
        return await self.start(name)

    async def stop_all(self) -> None:
        """Gracefully stop every running bot — called on API shutdown."""
        for bot in list(self._bots.values()):
            if bot.status == BotStatus.RUNNING:
                try:
                    await self.stop(bot.name)
                except Exception as exc:
                    logger.error("Error stopping bot '%s' on shutdown: %s", bot.name, exc)
