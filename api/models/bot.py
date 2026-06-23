"""
api/models/bot.py
=================
Pydantic schemas for bot management endpoints.

Interview talking points:
- BotStatus is a StrEnum so it serialises cleanly to JSON as a string
  while still providing autocomplete and exhaustive checks in Python code.
- Optional fields (pid, started_at, uptime_seconds) are None when a bot is
  stopped — the API consumer can check `status` first, then read those fields.
- BotActionResponse wraps every mutating action (start/stop/restart) in a
  consistent envelope, making client error handling uniform.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class BotStatus(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


class BotInfo(BaseModel):
    name: str
    folder: str
    entry_file: str
    status: BotStatus
    pid: Optional[int] = None
    started_at: Optional[datetime] = None
    uptime_seconds: Optional[float] = None


class BotActionResponse(BaseModel):
    success: bool
    message: str
    bot: Optional[BotInfo] = None
