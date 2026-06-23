"""
api/models/account.py
=====================
Pydantic schemas for OANDA account, position, trade, and signal endpoints.
Monetary values are kept as strings to avoid floating-point precision loss.
"""

from typing import Optional

from pydantic import BaseModel


class AccountSummary(BaseModel):
    id: str
    balance: str
    currency: str
    nav: str
    unrealized_pl: str
    margin_used: str
    margin_available: str
    open_trade_count: int
    open_position_count: int


class Position(BaseModel):
    instrument: str
    long_units: str
    long_pl: str
    short_units: str
    short_pl: str
    unrealized_pl: str


class Trade(BaseModel):
    id: str
    instrument: str
    price: str
    current_units: str
    realized_pl: str
    unrealized_pl: str
    open_time: str
    state: str


class SignalEntry(BaseModel):
    bot: str
    message: str
    timestamp: str
    instrument: Optional[str] = None
    event: Optional[str] = None
