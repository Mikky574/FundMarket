"""Request models shared by privileged API modules."""

from pydantic import BaseModel, Field


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


class PublicAiExperimentResetRequest(BaseModel):
    experiment_name: str = Field(min_length=1, max_length=120)
    start_date: str
    end_date: str
    initial_cash: str = "100000"
    user_confirmation: str = Field(min_length=1, max_length=1000)


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


class BlindGoldDecisionRequest(BaseModel):
    position: str
    rule_candidate: str
    observations: list[dict] = Field(min_length=1, max_length=20)
    analysis_mode: str = "technical_breakout"
