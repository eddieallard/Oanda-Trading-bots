"""
api/routes/account.py
=====================
OANDA account, position, trade, and signal endpoints.
OANDA API errors are mapped to 502 Bad Gateway. All routes require JWT auth.
"""

import logging
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.auth.dependencies import get_current_user
from api.models.account import AccountSummary, Position, SignalEntry, Trade
from api.services.oanda_service import (
    get_account_summary,
    get_open_positions,
    get_open_trades,
    get_recent_signals,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Account & Trading"])


@router.get(
    "/account",
    response_model=AccountSummary,
    summary="OANDA account summary",
)
async def account(_user: dict = Depends(get_current_user)) -> AccountSummary:
    try:
        return await get_account_summary()
    except httpx.HTTPStatusError as exc:
        logger.error("OANDA /account error: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OANDA API error")


@router.get(
    "/positions",
    response_model=list[Position],
    summary="Open positions",
)
async def positions(_user: dict = Depends(get_current_user)) -> list[Position]:
    try:
        return await get_open_positions()
    except httpx.HTTPStatusError as exc:
        logger.error("OANDA /positions error: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OANDA API error")


@router.get(
    "/trades",
    response_model=list[Trade],
    summary="Open trades",
)
async def trades(_user: dict = Depends(get_current_user)) -> list[Trade]:
    try:
        return await get_open_trades()
    except httpx.HTTPStatusError as exc:
        logger.error("OANDA /trades error: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="OANDA API error")


@router.get(
    "/signals",
    response_model=list[SignalEntry],
    summary="Recent signals from bot log files",
)
async def signals(
    request: Request,
    _user: dict = Depends(get_current_user),
) -> list[SignalEntry]:
    root_dir = str(Path(request.app.state.bot_manager.root))
    return await get_recent_signals(root_dir)
