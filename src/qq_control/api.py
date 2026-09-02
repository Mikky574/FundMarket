"""Local-only capabilities used by the QQ/Codex bridge.

Keeping these routes separate prevents public dashboard routes from acquiring
write capabilities accidentally.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from src.historical_evaluation import service as evaluation_service
from src.qq_control import ledger_control as public_ai_control
from src.qq_control.dependencies import require_local_bridge
from src.qq_control.schemas import (
    BlindGoldDecisionRequest,
    EvaluationMarketImport,
    EvaluationNewsImport,
    EvaluationPredictionRequest,
    EvaluationSessionRequest,
    PublicAiAnnotationRequest,
    PublicAiCancelOrderRequest,
    PublicAiDecisionRequest,
    PublicAiExperimentResetRequest,
    PublicAiOrderRequest,
    PublicAiSettleRequest,
)
from src.quant_research.intelligence import analyse_blind_gold, refresh_public_market_display
from src.qq_control.portfolio_view import get_portfolio_dashboard

router = APIRouter(prefix="/api/v1/internal", include_in_schema=False)


@router.post("/market-refresh")
async def market_refresh(request: Request):
    """Refresh research and display only; it never settles or trades."""
    require_local_bridge(request)
    try:
        refreshed = await run_in_threadpool(refresh_public_market_display)
        dashboard = get_portfolio_dashboard()
        return {"refresh": refreshed, "display_valuation": dashboard.get("display_valuation"),
                "summary": dashboard.get("summary"), "positions": dashboard.get("positions")}
    except Exception as exc:
        raise HTTPException(502, f"market refresh failed: {exc}") from exc


@router.post("/research/gold-blind-decision")
def gold_blind_decision(payload: BlindGoldDecisionRequest, request: Request):
    """Run a non-persistent, date-free DeepSeek classification for gold replay."""
    require_local_bridge(request)
    try:
        return analyse_blind_gold(**payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"blind gold research failed: {exc}") from exc


@router.get("/public-ai/portfolio")
def public_ai_portfolio(request: Request):
    require_local_bridge(request)
    return public_ai_control.portfolio()


@router.get("/public-ai/decisions")
def public_ai_decisions(request: Request):
    require_local_bridge(request)
    return {"items": public_ai_control.decisions()}


@router.get("/public-ai/orders")
def public_ai_orders(request: Request):
    require_local_bridge(request)
    return {"items": public_ai_control.orders()}


@router.post("/public-ai/experiments/archive-and-initialize")
def archive_and_initialize_experiment(payload: PublicAiExperimentResetRequest, request: Request):
    require_local_bridge(request)
    try:
        return public_ai_control.archive_and_initialize_experiment(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/public-ai/decisions")
def record_decision(payload: PublicAiDecisionRequest, request: Request):
    require_local_bridge(request)
    try:
        return public_ai_control.record_decision(payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/public-ai/decisions/{decision_id}/void")
def void_decision(decision_id: str, payload: PublicAiAnnotationRequest, request: Request):
    require_local_bridge(request)
    try:
        return public_ai_control.void_decision(decision_id, payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


def _order(kind: str, payload: PublicAiOrderRequest):
    return public_ai_control.buy(payload.model_dump()) if kind == "buy" else public_ai_control.sell(payload.model_dump())


@router.post("/public-ai/orders/buy")
@router.post("/public-ai/orders/add")
def buy(payload: PublicAiOrderRequest, request: Request):
    require_local_bridge(request)
    try:
        return _order("buy", payload)
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/public-ai/orders/reduce")
@router.post("/public-ai/orders/sell")
def sell(payload: PublicAiOrderRequest, request: Request):
    require_local_bridge(request)
    try:
        return _order("sell", payload)
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/public-ai/orders/liquidate/{fund_code}")
def liquidate(fund_code: str, payload: PublicAiOrderRequest, request: Request):
    require_local_bridge(request)
    try:
        return public_ai_control.liquidate(fund_code, payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/public-ai/orders/settle")
def settle(payload: PublicAiSettleRequest, request: Request):
    require_local_bridge(request)
    try:
        return public_ai_control.settle(payload.as_of)
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/public-ai/orders/{order_id}/cancel")
def cancel(order_id: str, payload: PublicAiCancelOrderRequest, request: Request):
    require_local_bridge(request)
    try:
        return public_ai_control.cancel_order(order_id, payload.model_dump())
    except (LookupError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/evaluation/market/import")
def import_market(payload: EvaluationMarketImport, request: Request):
    require_local_bridge(request)
    try:
        return evaluation_service.import_market(payload.rows)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/evaluation/news/import")
def import_news(payload: EvaluationNewsImport, request: Request):
    require_local_bridge(request)
    try:
        return evaluation_service.import_news(payload.items)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/evaluation/sessions")
def create_session(payload: EvaluationSessionRequest, request: Request):
    require_local_bridge(request)
    try:
        return evaluation_service.create_session(payload.as_of, payload.instruments, payload.initial_cash)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/evaluation/sessions/{session_id}")
def get_session(session_id: str, request: Request):
    require_local_bridge(request)
    try:
        return evaluation_service.session(session_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/evaluation/sessions/{session_id}/predictions")
def record_prediction(session_id: str, payload: EvaluationPredictionRequest, request: Request):
    require_local_bridge(request)
    try:
        return evaluation_service.record_prediction(session_id, payload.model_dump())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/evaluation/sessions/{session_id}/score")
def score(session_id: str, request: Request):
    require_local_bridge(request)
    try:
        return evaluation_service.score(session_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
