"""
api/routes/bots.py
==================
Bot lifecycle management endpoints.

Interview talking points:
- Request is injected by FastAPI to give access to app.state.  This is how
  shared services (BotManager) are accessed without global variables — they
  live on app.state, which is scoped to the application lifetime.
- All mutating operations (start/stop/restart) are POST.  GET /bots and
  GET /bots/status are read-only — correct use of HTTP semantics.
- ValueError (unknown bot) → 404,  RuntimeError (wrong state) → 409 Conflict.
  Mapping domain exceptions to the right HTTP status codes is a common
  interview question.
- Every route requires a valid JWT via Depends(get_current_user).  The
  dependency is declared once and FastAPI wires it automatically.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.auth.dependencies import get_current_user
from api.models.bot import BotActionResponse, BotInfo
from api.services.bot_manager import BotManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bots", tags=["Bot Management"])


def _manager(request: Request) -> BotManager:
    """Helper to extract the BotManager from app.state."""
    return request.app.state.bot_manager


@router.get("", response_model=list[BotInfo], summary="List all discovered bots")
async def list_bots(
    request: Request,
    _user: dict = Depends(get_current_user),
) -> list[BotInfo]:
    return _manager(request).list_all()


@router.get("/status", response_model=list[BotInfo], summary="Live status of all bots")
async def bot_statuses(
    request: Request,
    _user: dict = Depends(get_current_user),
) -> list[BotInfo]:
    mgr = _manager(request)
    await mgr.refresh_statuses()
    return mgr.list_all()


@router.post(
    "/start/{bot_name}",
    response_model=BotActionResponse,
    summary="Start a bot by name",
)
async def start_bot(
    bot_name: str,
    request: Request,
    _user: dict = Depends(get_current_user),
) -> BotActionResponse:
    try:
        info = await _manager(request).start(bot_name)
        logger.info("Bot '%s' started by user '%s'", bot_name, _user.get("sub"))
        return BotActionResponse(success=True, message=f"Bot '{bot_name}' started", bot=info)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/stop/{bot_name}",
    response_model=BotActionResponse,
    summary="Stop a running bot by name",
)
async def stop_bot(
    bot_name: str,
    request: Request,
    _user: dict = Depends(get_current_user),
) -> BotActionResponse:
    try:
        info = await _manager(request).stop(bot_name)
        logger.info("Bot '%s' stopped by user '%s'", bot_name, _user.get("sub"))
        return BotActionResponse(success=True, message=f"Bot '{bot_name}' stopped", bot=info)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/restart/{bot_name}",
    response_model=BotActionResponse,
    summary="Restart a bot by name",
)
async def restart_bot(
    bot_name: str,
    request: Request,
    _user: dict = Depends(get_current_user),
) -> BotActionResponse:
    try:
        info = await _manager(request).restart(bot_name)
        logger.info("Bot '%s' restarted by user '%s'", bot_name, _user.get("sub"))
        return BotActionResponse(success=True, message=f"Bot '{bot_name}' restarted", bot=info)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
