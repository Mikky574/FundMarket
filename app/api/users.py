"""Authenticated user, personal trading, and independent-AI routes."""

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import user_ai
from app.config import settings
from app.user_trading import (
    BuyOrder,
    Credentials,
    PresetPosition,
    SellOrder,
    cancel_order,
    create_buy,
    create_sell,
    current_user,
    login,
    logout,
    portfolio,
    preset_position,
    register,
)

router = APIRouter(prefix="/api/v1")


class UserAiPrompt(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


def _session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "market_session",
        token,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        samesite="strict",
        secure=settings.session_cookie_secure,
        path="/",
    )


@router.post("/auth/register", tags=["auth"])
def auth_register(credentials: Credentials, response: Response):
    user, token = register(credentials)
    _session_cookie(response, token)
    return user


@router.post("/auth/login", tags=["auth"])
def auth_login(credentials: Credentials, response: Response):
    user, token = login(credentials)
    _session_cookie(response, token)
    return user


@router.post("/auth/logout", tags=["auth"])
def auth_logout(response: Response, user: dict = Depends(current_user)):
    logout(user)
    response.delete_cookie("market_session", path="/")
    return {"status": "ok"}


@router.get("/auth/me", tags=["auth"])
def auth_me(user: dict = Depends(current_user)):
    return {"id": user["id"], "username": user["username"]}


@router.get("/user/portfolio", tags=["user trading"])
def user_portfolio(user: dict = Depends(current_user)):
    return portfolio(user["id"])


@router.post("/user/portfolio/refresh", tags=["user trading"])
def refresh_user_portfolio(user: dict = Depends(current_user)):
    return portfolio(user["id"], force_refresh=True)


@router.post("/user/positions/preset", tags=["user trading"])
def user_preset_position(position: PresetPosition, user: dict = Depends(current_user)):
    return preset_position(user["id"], position)


@router.get("/user/ai", tags=["user ai"])
def get_user_ai(user: dict = Depends(current_user)):
    return user_ai.view(user["id"])


@router.post("/user/ai/refresh", tags=["user ai"])
def refresh_user_ai(user: dict = Depends(current_user)):
    return user_ai.view(user["id"], force_refresh=True)


@router.post("/user/ai/sync", tags=["user ai"])
def sync_user_ai(confirmed: bool = False, user: dict = Depends(current_user)):
    return user_ai.create_or_sync(user["id"], confirmed)


@router.delete("/user/ai", tags=["user ai"])
def delete_user_ai(confirmed: bool = False, user: dict = Depends(current_user)):
    if not confirmed:
        raise HTTPException(422, "请明确确认删除独立 AI 的会话上下文和账本")
    user_ai.delete(user["id"])
    return {"status": "deleted"}


@router.delete("/user/ai/context", tags=["user ai"])
def clear_user_ai_context(confirmed: bool = False, user: dict = Depends(current_user)):
    if not confirmed:
        raise HTTPException(422, "请明确确认清空独立 AI 的对话上下文")
    user_ai.clear_context(user["id"])
    return {"status": "context_cleared"}


@router.post("/user/ai/ask", tags=["user ai"])
async def ask_user_ai(request: UserAiPrompt, user: dict = Depends(current_user)):
    return await run_in_threadpool(user_ai.ask, user["id"], request.prompt)


@router.post("/user/ai/ask/stream", tags=["user ai"])
def ask_user_ai_stream(request: UserAiPrompt, user: dict = Depends(current_user)):
    return StreamingResponse(
        user_ai.ask_stream(user["id"], request.prompt),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/user/orders/buy", tags=["user trading"])
def user_buy(order: BuyOrder, user: dict = Depends(current_user)):
    return create_buy(user["id"], order)


@router.post("/user/orders/sell", tags=["user trading"])
def user_sell(order: SellOrder, user: dict = Depends(current_user)):
    return create_sell(user["id"], order)


@router.post("/user/orders/{order_id}/cancel", tags=["user trading"])
def user_cancel(order_id: int, user: dict = Depends(current_user)):
    return cancel_order(user["id"], order_id)
