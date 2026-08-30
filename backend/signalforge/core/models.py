"""Pydantic domain contracts shared across the SignalForge backend."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

DecisionStatus = Literal["actionable", "needs_evidence", "refused"]
MemoJobStatus = Literal[
    "queued",
    "analyzing_signals",
    "grouping_evidence",
    "validating_evidence",
    "generating_memo",
    "completed",
    "failed",
]
TraceStage = Literal[
    "generation",
    "analyzing_signals",
    "grouping_evidence",
    "validating_evidence",
    "generating_memo",
]


class DatasetVersion(BaseModel):
    """An immutable imported snapshot used to scope every downstream claim."""

    id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    row_count: int = Field(ge=0)
    source_url: HttpUrl | None = None
    file_hash: str | None = None
    imported_at: datetime | None = None
    license_name: str | None = None


class Evidence(BaseModel):
    """A retrievable source fragment that may support an insight or decision."""

    id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_type: Literal["review", "market_event"] = "review"
    rating: int | None = Field(default=None, ge=1, le=5)
    aspect: str | None = None
    sentiment: Literal["positive", "neutral", "negative", "unknown"] = "unknown"
    relevance_score: float | None = Field(default=None, ge=0, le=1)
    redacted: bool = False


class Insight(BaseModel):
    """An evidence-bound product or market observation."""

    id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    unknowns: list[str] = Field(default_factory=list)
    recommended_action: Literal["investigate", "experiment", "monitor"] = "investigate"
    generation_method: Literal["deterministic", "llm", "fallback"] = "deterministic"
    status: Literal["draft", "validated", "rejected"] = "draft"


class DecisionCard(BaseModel):
    """An explainable action proposal linked to the exact evidence version."""

    id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    primary_metric: str = Field(min_length=1)
    guardrail_metric: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    due_date: date
    score: float | None = Field(default=None, ge=0, le=100)
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    status: Literal["draft", "planned", "in_progress", "completed", "cancelled"] = "draft"


class _StrictMemoModel(BaseModel):
    """Reject silent shape drift in model-generated decision-memo payloads."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MemoFacts(_StrictMemoModel):
    """Deterministic topic facts injected by SignalForge, never calculated by a model."""

    review_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    negative_rate: float | None = Field(ge=0, le=100)
    average_rating: float | None = Field(default=None, ge=1, le=5)
    severity: float | None = Field(default=None, ge=0, le=100)
    affected: float | None = Field(default=None, ge=0, le=100)
    evidence: float | None = Field(default=None, ge=0, le=100)
    opportunity_score: float | None = Field(default=None, ge=0, le=100)
    scoreable: bool = True
    missing_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "MemoFacts":
        if self.negative_count > self.review_count:
            raise ValueError("negative_count cannot exceed review_count")
        return self


class MemoEvidenceReference(_StrictMemoModel):
    """A compact, version-scoped citation and its role in the memo."""

    evidence_id: str = Field(min_length=1, max_length=128)
    rationale: str = Field(min_length=1, max_length=300)


class ExperimentPlan(_StrictMemoModel):
    """A complete, falsifiable experiment emitted only for actionable memos."""

    hypothesis: str = Field(min_length=1, max_length=1000)
    target_segment: str = Field(min_length=1, max_length=300)
    intervention: str = Field(min_length=1, max_length=1000)
    primary_metric: str = Field(min_length=1, max_length=300)
    guardrail_metric: str = Field(min_length=1, max_length=300)
    duration_days: int = Field(ge=1, le=365)
    stop_conditions: list[str] = Field(min_length=1, max_length=10)


class EvidencePlan(_StrictMemoModel):
    """Concrete collection work required before a business action can be recommended."""

    candidate_subproblems: list[str] = Field(min_length=1, max_length=10)
    collection_fields: list[str] = Field(min_length=1, max_length=20)
    minimum_evidence_per_subproblem: int = Field(ge=3, le=1000)
    reassessment_condition: str = Field(min_length=1, max_length=500)


class DecisionMemo(_StrictMemoModel):
    """One evidence-validated primary decision for an immutable dataset version."""

    id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    decision_status: DecisionStatus
    decision_statement: str = Field(min_length=1, max_length=1000)
    topic: str = Field(min_length=1, max_length=160)
    subproblem: str | None = Field(default=None, min_length=1, max_length=300)
    facts: MemoFacts
    supporting_evidence: list[MemoEvidenceReference]
    counter_evidence: list[MemoEvidenceReference]
    counter_evidence_checked: bool
    unknowns: list[str] = Field(min_length=1, max_length=20)
    reasoning_summary: str = Field(min_length=1, max_length=2000)
    experiment: ExperimentPlan | None = None
    evidence_plan: EvidencePlan | None = None
    refusal_reason: str | None = Field(default=None, min_length=1, max_length=1000)
    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def state_specific_fields_are_valid(self) -> "DecisionMemo":
        if (
            self.decision_status in {"actionable", "needs_evidence"}
            and not self.counter_evidence_checked
        ):
            raise ValueError(
                "counter_evidence_checked must be true for an actionable or "
                "needs_evidence memo"
            )
        if (
            self.decision_status == "refused"
            and not self.counter_evidence_checked
            and self.refusal_reason != "COUNTER_EVIDENCE_NOT_CHECKED"
        ):
            raise ValueError(
                "an unchecked refusal requires COUNTER_EVIDENCE_NOT_CHECKED"
            )
        if self.decision_status == "actionable":
            if self.experiment is None:
                raise ValueError("experiment is required for an actionable memo")
            if self.subproblem is None:
                raise ValueError("subproblem is required for an actionable memo")
            if self.evidence_plan is not None:
                raise ValueError("evidence_plan is only valid for a needs_evidence memo")
            if self.refusal_reason is not None:
                raise ValueError("refusal_reason is only valid for a refused memo")
        elif self.decision_status == "needs_evidence":
            if self.evidence_plan is None:
                raise ValueError("evidence_plan is required for a needs_evidence memo")
            if self.experiment is not None:
                raise ValueError("experiment is only valid for an actionable memo")
            if self.refusal_reason is not None:
                raise ValueError("refusal_reason is only valid for a refused memo")
            if self.subproblem is not None:
                raise ValueError("subproblem is only valid for an actionable memo")
        else:
            if self.refusal_reason is None:
                raise ValueError("refusal_reason is required for a refused memo")
            if self.experiment is not None:
                raise ValueError("experiment is only valid for an actionable memo")
            if self.evidence_plan is not None:
                raise ValueError("evidence_plan is only valid for a needs_evidence memo")
            if self.subproblem is not None:
                raise ValueError("subproblem is only valid for an actionable memo")
        return self


class MemoGenerationJob(_StrictMemoModel):
    """Persisted state for one real, asynchronous memo-generation attempt."""

    id: str = Field(min_length=1, max_length=128)
    dataset_version_id: str = Field(min_length=1, max_length=128)
    status: MemoJobStatus = "queued"
    memo_id: str | None = Field(default=None, min_length=1, max_length=128)
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def terminal_state_fields_are_valid(self) -> "MemoGenerationJob":
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.status == "completed":
            if self.memo_id is None:
                raise ValueError("memo_id is required for a completed job")
            if self.error_code is not None:
                raise ValueError("error_code is only valid for a failed job")
        elif self.status == "failed":
            if self.error_code is None:
                raise ValueError("error_code is required for a failed job")
            if self.memo_id is not None:
                raise ValueError("memo_id is only valid for a completed job")
        elif self.memo_id is not None or self.error_code is not None:
            raise ValueError("memo_id and error_code are only valid for terminal jobs")
        return self


class MarketEvent(BaseModel):
    """A manually reviewed authority-source event used by the risk radar."""

    id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    source_url: HttpUrl
    published_on: date
    excerpt: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    industry: str = Field(min_length=1)
    evidence_quality: int = Field(ge=0, le=100)


class TraceRecord(BaseModel):
    """Auditable metadata for an insight-generation or validation attempt."""

    id: str = Field(min_length=1)
    entity_type: Literal["insight", "decision", "risk", "memo"]
    entity_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    model_name: str | None = None
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    stage: TraceStage = "generation"
    retry_count: int = Field(default=0, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)
    validation_status: Literal[
        "accepted",
        "refused",
        "invalid",
        "retried",
        "fallback",
        "numeric_mismatch",
        "failed",
    ]
    latency_ms: int = Field(ge=0)
    token_estimate: int = Field(ge=0)
    created_at: datetime
