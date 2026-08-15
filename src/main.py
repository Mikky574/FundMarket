from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.fund_service import get_fund_overview, search_funds
from app.models import ErrorResponse, HistoryResponse, Quote, StockItem
from app.market_store import cache_stats
from app.portfolio_service import get_portfolio_dashboard
from app.quant_service import market_brief
from app.market_intelligence import (latest as latest_market_intelligence, refresh_public_market_display as refresh_public_market_artifacts,
                                     watchlist as market_watchlist)
from app.providers.akshare_provider import AkShareProvider
from app.service import StockService, normalize_symbol
from app.user_trading import (BuyOrder, Credentials, SellOrder, cancel_order, create_buy,
                              create_sell, current_user, login, logout, portfolio, preset_position, register,
                              PresetPosition)
from app import user_ai
from app import public_ai_control
from app import evaluation_service
from app.api.internal import router as internal_router
from app.api.users import router as users_router
from pydantic import BaseModel, Field

app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description="中国 A 股统一行情接口。数据仅供研究，不构成投资建议。",
)
service = StockService(AkShareProvider())
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(internal_router)
app.include_router(users_router)


def get_service() -> StockService:
    return service


def _session_cookie(response: Response, token: str) -> None:
    response.set_cookie("market_session", token, max_age=30 * 24 * 60 * 60, httponly=True,
                        samesite="strict", secure=settings.session_cookie_secure, path="/")


def auth_register(credentials: Credentials, response: Response):
    user, token = register(credentials); _session_cookie(response, token)
    return user


def auth_login(credentials: Credentials, response: Response):
    user, token = login(credentials); _session_cookie(response, token)
    return user


def auth_logout(response: Response, user: dict = Depends(current_user)):
    logout(user); response.delete_cookie("market_session", path="/")
    return {"status": "ok"}


def auth_me(user: dict = Depends(current_user)):
    return {"id": user["id"], "username": user["username"]}


def user_portfolio(user: dict = Depends(current_user)):
    return portfolio(user["id"])


def refresh_user_portfolio(user: dict = Depends(current_user)):
    return portfolio(user["id"], force_refresh=True)


def user_preset_position(position: PresetPosition, user: dict = Depends(current_user)):
    return preset_position(user["id"], position)


class UserAiPrompt(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)


class PublicAiDecisionRequest(BaseModel):
    decision_id: str | None = None
    decision_date: str | None = None
    data_as_of: str | None = None
    action: str
    market_observation: str
    reason: str
    confidence: int = Field(ge=0, le=100)
    evidence: list[str] = []
    counter_evidence: str = ""
    invalidation_conditions: str = ""
    user_confirmation: str


class PublicAiAnnotationRequest(BaseModel):
    annotation_id: str | None = None
    status: str = "VOIDED"
    reason: str
    user_confirmation: str


class PublicAiOrderRequest(BaseModel):
    order_id: str | None = None
    decision_id: str
    decision_date: str | None = None
    fund_code: str
    fund_name: str | None = None
    amount: str | None = None
    shares: str | None = None
    subscription_fee_rate: str = "0"
    thesis: str
    evidence: list[str] = []


class PublicAiSettleRequest(BaseModel):
    as_of: str


class PublicAiCancelOrderRequest(BaseModel):
    reason: str
    user_confirmation: str


class EvaluationMarketImport(BaseModel):
    rows: list[dict]


class EvaluationNewsImport(BaseModel):
    items: list[dict]


class EvaluationSessionRequest(BaseModel):
    as_of: str
    instruments: list[str] = Field(min_length=1)
    initial_cash: str = "100000"


class EvaluationPredictionRequest(BaseModel):
    instrument: str
    direction: str
    confidence: int = Field(ge=0, le=100)
    horizon_trading_days: int = Field(gt=0)
    expected_return_range_percent: list[float] | None = None
    rationale: str = ""


def _require_local_bridge(request: Request) -> None:
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "local bridge only")


def get_user_ai(user: dict = Depends(current_user)):
    return user_ai.view(user["id"])


def refresh_user_ai(user: dict = Depends(current_user)):
    return user_ai.view(user["id"], force_refresh=True)


def sync_user_ai(confirmed: bool = False, user: dict = Depends(current_user)):
    return user_ai.create_or_sync(user["id"], confirmed)


def delete_user_ai(confirmed: bool = False, user: dict = Depends(current_user)):
    if not confirmed:
        raise HTTPException(422, "请明确确认删除独立 AI 的会话上下文和账本")
    user_ai.delete(user["id"])
    return {"status": "deleted"}


def clear_user_ai_context(confirmed: bool = False, user: dict = Depends(current_user)):
    if not confirmed:
        raise HTTPException(422, "请明确确认清空独立 AI 的对话上下文")
    user_ai.clear_context(user["id"])
    return {"status": "context_cleared"}


async def ask_user_ai(request: UserAiPrompt, user: dict = Depends(current_user)):
    return await run_in_threadpool(user_ai.ask, user["id"], request.prompt)


def ask_user_ai_stream(request: UserAiPrompt, user: dict = Depends(current_user)):
    return StreamingResponse(user_ai.ask_stream(user["id"], request.prompt), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def user_buy(order: BuyOrder, user: dict = Depends(current_user)):
    return create_buy(user["id"], order)


def user_sell(order: SellOrder, user: dict = Depends(current_user)):
    return create_sell(user["id"], order)


def user_cancel(order_id: int, user: dict = Depends(current_user)):
    return cancel_order(user["id"], order_id)


@app.get("/", include_in_schema=False)
def dashboard():
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    html = html.replace("dashboard.js?v=20260726-14", "dashboard.js?v=20260804-23")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/v1/portfolio", tags=["portfolio"])
def portfolio_dashboard():
    try:
        return get_portfolio_dashboard()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/v1/research/market-brief", tags=["research"])
async def research_market_brief(
    funds: str = Query(..., description="逗号分隔的 6 位基金代码，最多 12 只"),
    refresh: bool = Query(False, description="强制重新拉取公开净值"),
):
    """Read-only, dated quantitative research packet for QQ and dashboards."""
    try:
        return await run_in_threadpool(market_brief, funds.split(","), refresh)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/v1/research/market-intelligence", tags=["research"])
def research_market_intelligence():
    """Read-only latest DeepSeek interpretation of dated market evidence."""
    return latest_market_intelligence()


@app.get("/api/v1/research/watchlist", tags=["research"])
def research_watchlist():
    """Read-only shared public/anonymous-customer market watch registry."""
    return market_watchlist()


async def refresh_public_market_display(request: Request):
    """Local QQ bridge refresh: research/display artifacts only, never ledger state."""
    _require_local_bridge(request)
    try:
        refreshed = await run_in_threadpool(refresh_public_market_artifacts)
        dashboard = get_portfolio_dashboard()
        return {"refresh": refreshed, "display_valuation": dashboard.get("display_valuation"),
                "summary": dashboard.get("summary"), "positions": dashboard.get("positions")}
    except Exception as exc:
        raise HTTPException(502, f"market refresh failed: {exc}") from exc


def public_ai_portfolio(request: Request):
    _require_local_bridge(request)
    return public_ai_control.portfolio()


def public_ai_decisions(request: Request):
    _require_local_bridge(request)
    return {"items": public_ai_control.decisions()}


def public_ai_orders(request: Request):
    _require_local_bridge(request)
    return {"items": public_ai_control.orders()}


def public_ai_record_decision(payload: PublicAiDecisionRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.record_decision(payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_ai_void_decision(decision_id: str, payload: PublicAiAnnotationRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.void_decision(decision_id, payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_ai_buy(payload: PublicAiOrderRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.buy(payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_ai_add(payload: PublicAiOrderRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.buy(payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_ai_reduce(payload: PublicAiOrderRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.sell(payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_ai_sell(payload: PublicAiOrderRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.sell(payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_ai_liquidate(fund_code: str, payload: PublicAiOrderRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.liquidate(fund_code, payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_ai_cancel_order(order_id: str, payload: PublicAiCancelOrderRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.cancel_order(order_id, payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def public_ai_settle(payload: PublicAiSettleRequest, request: Request):
    _require_local_bridge(request)
    try:
        return public_ai_control.settle(payload.as_of)
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def evaluation_import_market(payload: EvaluationMarketImport, request: Request):
    _require_local_bridge(request)
    try:
        return evaluation_service.import_market(payload.rows)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def evaluation_import_news(payload: EvaluationNewsImport, request: Request):
    _require_local_bridge(request)
    try:
        return evaluation_service.import_news(payload.items)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def evaluation_create_session(payload: EvaluationSessionRequest, request: Request):
    _require_local_bridge(request)
    try:
        return evaluation_service.create_session(payload.as_of, payload.instruments, payload.initial_cash)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def evaluation_get_session(session_id: str, request: Request):
    _require_local_bridge(request)
    try:
        return evaluation_service.session(session_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


def evaluation_record_prediction(session_id: str, payload: EvaluationPredictionRequest, request: Request):
    _require_local_bridge(request)
    try:
        return evaluation_service.record_prediction(session_id, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def evaluation_score(session_id: str, request: Request):
    _require_local_bridge(request)
    try:
        return evaluation_service.score(session_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/fund", include_in_schema=False)
def fund_dashboard():
    return FileResponse("app/static/fund.html")


@app.get("/api/v1/funds/{code}", tags=["funds"])
async def fund_overview(code: str):
    try:
        return await run_in_threadpool(get_fund_overview, code)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        message = str(exc)
        if "Unexpected token '<'" in message or "JSParse" in type(exc).__name__:
            raise HTTPException(502, "该基金份额暂不支持净值预览；若名称包含“后端”，该份额通常不可申购，请选择对应的普通 A/C/D 份额") from exc
        raise HTTPException(502, "基金净值数据暂时不可用，请稍后重试或选择其他份额") from exc


@app.get("/api/v1/funds", tags=["funds"])
async def fund_search(q: str = Query(..., min_length=1), page: int = Query(1, ge=1),
                      page_size: int = Query(50, ge=1, le=50), tradable_only: bool = False):
    try:
        matches = await run_in_threadpool(search_funds, q, 100_000)
        if tradable_only:
            matches = [item for item in matches if item.get("previewable") and item.get("purchasable")]
        total = len(matches)
        start = (page - 1) * page_size
        return {"items": matches[start:start + page_size],
                "pagination": {"page": page, "page_size": page_size, "total": total,
                               "total_pages": max(1, (total + page_size - 1) // page_size)}}
    except Exception as exc:
        raise HTTPException(502, f"基金列表请求失败: {exc}") from exc


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "provider": "akshare",
            "market_refresh_seconds": settings.market_refresh_seconds, "cache": cache_stats()}


@app.get("/api/v1/stocks", tags=["stocks"])
async def list_stocks(
    q: str | None = Query(None, description="代码或名称，例如 600000 或 浦发"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=50),
    svc: StockService = Depends(get_service),
):
    try:
        total = await run_in_threadpool(svc.stock_count, q)
        items = await run_in_threadpool(svc.stocks, q, page_size, (page - 1) * page_size)
        return {"items": items,
                "pagination": {"page": page, "page_size": page_size, "total": total,
                               "total_pages": max(1, (total + page_size - 1) // page_size)}}
    except Exception as exc:
        raise HTTPException(502, f"行情源请求失败: {exc}") from exc


@app.get("/api/v1/stocks/{symbol}/quote", response_model=Quote, responses={404: {"model": ErrorResponse}}, tags=["stocks"])
async def get_quote(symbol: str, svc: StockService = Depends(get_service)):
    try:
        result = await run_in_threadpool(svc.quote, symbol)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"行情源请求失败: {exc}") from exc
    if result is None:
        raise HTTPException(404, f"股票 {symbol} 不存在")
    return result


@app.get("/api/v1/stocks/{symbol}/history", response_model=HistoryResponse, tags=["stocks"])
async def get_history(
    symbol: str,
    start: date = Query(default_factory=lambda: date.today() - timedelta(days=30)),
    end: date = Query(default_factory=date.today),
    period: Literal["daily", "weekly", "monthly"] = "daily",
    adjust: Literal["", "qfq", "hfq"] = Query("qfq", description="空=不复权，qfq=前复权，hfq=后复权"),
    svc: StockService = Depends(get_service),
):
    if start > end:
        raise HTTPException(422, "start 不能晚于 end")
    try:
        data = await run_in_threadpool(svc.history, symbol, start, end, period, adjust)
        normalized = normalize_symbol(symbol)
        return HistoryResponse(symbol=normalized, period=period, adjust=adjust, data=data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"行情源请求失败: {exc}") from exc
