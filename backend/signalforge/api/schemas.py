"""Pydantic HTTP contracts.  These schemas deliberately exclude internal prompts and secrets."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator

from signalforge.core.models import Insight, TraceRecord

Sentiment = Literal["positive", "neutral", "negative", "unknown"]
FeedbackEntityType = Literal["insight", "decision", "risk"]
FeedbackDecision = Literal["confirmed", "rejected", "edited"]
EventType = Literal["policy", "market", "talent"]


class DatasetMetadataResponse(BaseModel):
    id: str
    source_name: str
    source_url: HttpUrl | None = None
    file_hash: str | None = None
    row_count: int = Field(ge=0)
    imported_at: datetime
    is_stale: bool


class PaginationResponse(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class SummaryMetricsResponse(BaseModel):
    review_count: int = Field(ge=0)
    negative_review_count: int = Field(ge=0)
    negative_review_rate: float | None = Field(default=None, ge=0, le=100)
    topic_count: int = Field(ge=0)
    market_event_count: int = Field(ge=0)


class OpportunityCardResponse(BaseModel):
    id: str
    title: str
    dataset_version_id: str
    aspect: str
    review_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    negative_rate: float | None = Field(default=None, ge=0, le=100)
    score: float | None = Field(default=None, ge=0, le=100)
    scoreable: bool
    missing_fields: list[str] = Field(default_factory=list)
    contributions: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_count: int = Field(ge=0)


class RiskCardResponse(BaseModel):
    id: str
    dataset_version_id: str
    source_url: HttpUrl
    published_on: date
    excerpt: str
    event_type: EventType
    industry: str
    evidence_quality: int = Field(ge=0, le=100)
    score: float = Field(ge=0, le=100)
    contributions: dict[str, float]
    mitigation_action: str
    owner: str
    status: Literal["monitoring", "needs_review"] = "monitoring"


class OverviewResponse(BaseModel):
    active_dataset: DatasetMetadataResponse
    summary_metrics: SummaryMetricsResponse
    opportunities: list[OpportunityCardResponse] = Field(max_length=3)
    risks: list[RiskCardResponse] = Field(max_length=2)


class TopicMetricResponse(BaseModel):
    aspect: str
    review_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    negative_rate: float | None = Field(default=None, ge=0, le=100)
    average_rating: float | None = Field(default=None, ge=1, le=5)
    severity: float | None = Field(default=None, ge=0, le=100)
    affected: float | None = Field(default=None, ge=0, le=100)
    evidence: float | None = Field(default=None, ge=0, le=100)
    scoreable: bool
    missing_fields: list[str] = Field(default_factory=list)


class InsightsResponse(BaseModel):
    active_dataset: DatasetMetadataResponse
    metrics: list[TopicMetricResponse]
    insights: list[Insight]
    pagination: PaginationResponse


class GenerateInsightBody(BaseModel):
    dataset_version_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=300)
    limit: int = Field(default=8, ge=1, le=8)


class GeneratedInsightResponse(BaseModel):
    insight: Insight
    evidence_count: int = Field(ge=2)


class GenerateDecisionMemoBody(BaseModel):
    """Request one local, evidence-bound memo for an immutable snapshot."""

    dataset_version_id: str = Field(min_length=1, max_length=128)


class CreateDecisionBody(BaseModel):
    dataset_version_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=120)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    problem_statement: str = Field(min_length=1, max_length=1000)
    hypothesis: str = Field(min_length=1, max_length=1000)
    primary_metric: str = Field(min_length=1, max_length=160)
    guardrail_metric: str = Field(min_length=1, max_length=160)
    owner: str = Field(min_length=1, max_length=80)
    due_date: date
    business_fit: float = Field(default=70, ge=0, le=100)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> "CreateDecisionBody":
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence_ids must be unique")
        return self


class FeedbackBody(BaseModel):
    entity_type: FeedbackEntityType
    entity_id: str = Field(min_length=1, max_length=128)
    decision: FeedbackDecision
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def edited_feedback_requires_reason(self) -> "FeedbackBody":
        if self.decision == "edited" and not (self.reason and self.reason.strip()):
            raise ValueError("reason is required when decision is edited")
        return self


class FeedbackResponse(BaseModel):
    id: str
    entity_type: FeedbackEntityType
    entity_id: str
    decision: FeedbackDecision
    reason: str | None = None
    created_at: datetime


class RisksResponse(BaseModel):
    active_dataset: DatasetMetadataResponse
    risks: list[RiskCardResponse]
    pagination: PaginationResponse


class TracesResponse(BaseModel):
    entity_id: str
    traces: list[TraceRecord]
    pagination: PaginationResponse


class HealthResponse(BaseModel):
    status: Literal["ok"]
    database: Literal["ready"]


class ModelHealthResponse(BaseModel):
    configured: bool
    ready: bool
    provider: Literal["ollama"]
    model_name: str | None = None
    error_code: str | None = None
