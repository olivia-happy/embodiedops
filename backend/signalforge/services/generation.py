"""Evidence-bound structured insight generation with an offline safe fallback."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from time import perf_counter
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from signalforge.core.models import Evidence
from signalforge.db.connection import Database
from signalforge.db.repositories import get_evidence
from signalforge.services.tracing import estimate_tokens, persist_generation_trace

PROMPT_VERSION = "insight-v1"
MINIMUM_EVIDENCE = 2


class GeneratedInsight(BaseModel):
    """The sole accepted provider response contract.

    ``strict`` and ``extra=forbid`` prevent a provider from quietly returning a
    partial/free-form response that callers might mistake for a validated claim.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    claim: str = Field(min_length=1, max_length=600)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0, le=1)
    unknowns: list[str] = Field(max_length=8)
    recommended_action: Literal["investigate", "experiment", "monitor"]

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_must_be_unique(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("evidence_ids must not contain blank values")
        if len(values) != len(set(values)):
            raise ValueError("evidence_ids must be unique")
        return values


class Refusal(BaseModel):
    """A safe, explicit response when available evidence cannot support a claim."""

    model_config = ConfigDict(extra="forbid", strict=True)

    code: Literal["INSUFFICIENT_EVIDENCE"] = "INSUFFICIENT_EVIDENCE"
    message: str
    evidence_count: int = Field(ge=0)
    required_count: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of schema and citation checks performed before any claim is saved."""

    accepted: bool
    reason: str | None = None
    insight: GeneratedInsight | None = None


class InsightProvider(Protocol):
    """Optional adapter boundary.  Providers must return a JSON object, not prose."""

    model_name: str | None

    def generate(self, *, prompt: str) -> Mapping[str, object] | str:
        """Generate one JSON-compatible structured response."""


@dataclass(frozen=True, slots=True)
class StrictJsonProviderAdapter:
    """Adapt an optional provider callback while rejecting non-object JSON replies.

    The callback is deliberately injected: this project has no default network
    model path, so local demos cannot transmit review text unexpectedly.  An
    application that elects to configure a remote provider owns that transport.
    """

    responder: Callable[[str], Mapping[str, object] | str]
    model_name: str | None = None

    def generate(self, *, prompt: str) -> Mapping[str, object]:
        output = self.responder(prompt)
        if isinstance(output, str):
            parsed = json.loads(output)
        else:
            parsed = dict(output)
        if not isinstance(parsed, dict):
            raise ValueError("provider response must be a JSON object")
        return parsed


@dataclass(frozen=True, slots=True)
class GenerateInsightRequest:
    """All context required to create a version-scoped insight without global state."""

    dataset_version_id: str
    query: str
    evidence: list[Evidence]
    db: Database | None = None
    entity_id: str = field(default_factory=lambda: str(uuid4()))
    prompt_version: str = PROMPT_VERSION

    def __post_init__(self) -> None:
        if not self.dataset_version_id.strip():
            raise ValueError("dataset_version_id cannot be blank")
        if not self.query.strip():
            raise ValueError("query cannot be blank")
        if any(item.dataset_version_id != self.dataset_version_id for item in self.evidence):
            raise ValueError("all evidence must belong to the requested dataset version")


def validate_generated_insight(
    result: GeneratedInsight | Mapping[str, object], *, allowed_evidence_ids: set[str]
) -> ValidationResult:
    """Reject malformed output and fabricated/unscopeable citations deterministically."""

    try:
        insight = (
            result
            if isinstance(result, GeneratedInsight)
            else GeneratedInsight.model_validate(result, strict=True)
        )
    except (ValidationError, TypeError, ValueError):
        return ValidationResult(accepted=False, reason="invalid_structured_output")

    if not set(insight.evidence_ids).issubset(allowed_evidence_ids):
        return ValidationResult(accepted=False, reason="unknown_evidence_id")
    return ValidationResult(accepted=True, insight=insight)


def _prompt(request: GenerateInsightRequest) -> str:
    """Create a bounded provider instruction; this string is never persisted."""

    evidence_lines = "\n".join(
        f"- id={item.id}; aspect={item.aspect or 'unknown'}; sentiment={item.sentiment}; "
        f"text={item.content[:500]}"
        for item in request.evidence[:8]
    )
    allowed = ", ".join(item.id for item in request.evidence[:8])
    return (
        "You are a product-insight assistant. Return JSON only, matching exactly: "
        "claim, evidence_ids, confidence, unknowns, recommended_action. "
        f"You may cite only these evidence IDs: [{allowed}]. "
        "Do not invent facts, metrics, sources, or citations. "
        f"User question: {request.query}\nEvidence:\n{evidence_lines}"
    )


def _deterministic_summary(request: GenerateInsightRequest) -> GeneratedInsight:
    """Create an explainable no-network result from the retrieved evidence alone."""

    evidence = request.evidence[:8]
    negative_count = sum(item.sentiment == "negative" for item in evidence)
    aspect = next((item.aspect for item in evidence if item.aspect), "该主题")
    if negative_count:
        claim = (
            f"检索到的 {len(evidence)} 条证据中有 {negative_count} 条负向反馈，"
            f"“{aspect}”问题值得进一步验证。"
        )
        action: Literal["investigate", "experiment", "monitor"] = "investigate"
    else:
        claim = f"检索到 {len(evidence)} 条与“{request.query}”相关的证据，暂未发现一致的负向信号。"
        action = "monitor"
    return GeneratedInsight(
        claim=claim,
        evidence_ids=[item.id for item in evidence],
        confidence=round(min(0.85, 0.45 + len(evidence) * 0.1), 2),
        unknowns=["结论仅基于当前关键词检索样本，不能代表全部用户。"],
        recommended_action=action,
    )


def _refusal(request: GenerateInsightRequest, started_at: float) -> Refusal:
    """Return and trace the evidence threshold refusal without calling a provider."""

    persist_generation_trace(
        request.db,
        entity_id=request.entity_id,
        dataset_version_id=request.dataset_version_id,
        prompt_version=request.prompt_version,
        evidence_ids=[item.id for item in request.evidence],
        validation_status="refused",
        latency_ms=round((perf_counter() - started_at) * 1000),
        token_estimate=estimate_tokens(request.query, *(item.content for item in request.evidence)),
    )
    return Refusal(
        message="证据不足，建议补充样本或调整筛选条件。",
        evidence_count=len(request.evidence),
        required_count=MINIMUM_EVIDENCE,
    )


def _active_evidence(request: GenerateInsightRequest) -> GenerateInsightRequest:
    """Keep only distinct IDs that exist in the active DuckDB snapshot.

    A caller may pass an old UI selection or hand-built test object.  When a
    database is available, generated claims are constrained to IDs currently
    persisted for the requested immutable version, not just IDs supplied by a
    client object.
    """

    unique: list[Evidence] = []
    seen_ids: set[str] = set()
    for item in request.evidence:
        if item.id not in seen_ids:
            unique.append(item)
            seen_ids.add(item.id)
    if request.db is None:
        return replace(request, evidence=unique)
    stored = get_evidence(request.db, request.dataset_version_id, [item.id for item in unique])
    allowed_ids = {str(item["id"]) for item in stored}
    return replace(request, evidence=[item for item in unique if item.id in allowed_ids])


def _try_provider(
    request: GenerateInsightRequest, provider: InsightProvider, started_at: float
) -> GeneratedInsight | None:
    """Make one validated provider attempt and persist its outcome metadata."""

    allowed_ids = {item.id for item in request.evidence}
    request_evidence_ids = [item.id for item in request.evidence]
    token_estimate = estimate_tokens(request.query, *(item.content for item in request.evidence))
    model_name = provider.model_name
    try:
        output = provider.generate(prompt=_prompt(request))
    except Exception:
        persist_generation_trace(
            request.db,
            entity_id=request.entity_id,
            dataset_version_id=request.dataset_version_id,
            prompt_version=request.prompt_version,
            evidence_ids=request_evidence_ids,
            validation_status="invalid",
            latency_ms=round((perf_counter() - started_at) * 1000),
            token_estimate=token_estimate,
            model_name=model_name,
        )
        return None
    validation = validate_generated_insight(output, allowed_evidence_ids=allowed_ids)
    persist_generation_trace(
        request.db,
        entity_id=request.entity_id,
        dataset_version_id=request.dataset_version_id,
        prompt_version=request.prompt_version,
        evidence_ids=(
            validation.insight.evidence_ids if validation.insight else request_evidence_ids
        ),
        validation_status="accepted" if validation.accepted else "invalid",
        latency_ms=round((perf_counter() - started_at) * 1000),
        token_estimate=token_estimate,
        model_name=model_name,
    )
    return validation.insight if validation.accepted else None


def generate_insight(
    request: GenerateInsightRequest, *, provider: InsightProvider | None = None
) -> GeneratedInsight | Refusal:
    """Generate a validated insight or an explicit safe refusal.

    The optional provider gets one retry after an invalid response.  Any outage,
    schema error, or fabricated citation falls back to a deterministic summary;
    a network provider is never required for normal local operation.
    """

    request = _active_evidence(request)
    started_at = perf_counter()
    if len(request.evidence) < MINIMUM_EVIDENCE:
        return _refusal(request, started_at)

    if provider is not None:
        accepted = _try_provider(request, provider, started_at)
        if accepted is not None:
            return accepted
        persist_generation_trace(
            request.db,
            entity_id=request.entity_id,
            dataset_version_id=request.dataset_version_id,
            prompt_version=request.prompt_version,
            evidence_ids=[item.id for item in request.evidence],
            validation_status="retried",
            latency_ms=round((perf_counter() - started_at) * 1000),
            token_estimate=estimate_tokens(
                request.query, *(item.content for item in request.evidence)
            ),
            model_name=provider.model_name,
        )
        retry = _try_provider(request, provider, started_at)
        if retry is not None:
            return retry

    fallback = _deterministic_summary(request)
    # The fallback is built from local evidence, then still passes the same
    # citation check so it cannot bypass the safety invariant.
    validation = validate_generated_insight(
        fallback, allowed_evidence_ids={item.id for item in request.evidence}
    )
    if not validation.accepted or validation.insight is None:  # defensive invariant guard
        raise RuntimeError("deterministic fallback violated its evidence contract")
    persist_generation_trace(
        request.db,
        entity_id=request.entity_id,
        dataset_version_id=request.dataset_version_id,
        prompt_version=request.prompt_version,
        evidence_ids=fallback.evidence_ids,
        validation_status="fallback",
        latency_ms=round((perf_counter() - started_at) * 1000),
        token_estimate=estimate_tokens(request.query, *(item.content for item in request.evidence)),
    )
    return fallback
