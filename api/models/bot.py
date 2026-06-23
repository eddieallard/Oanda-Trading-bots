"""
api/models/bot.py
=================
Pydantic schemas for bot management endpoints.
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
