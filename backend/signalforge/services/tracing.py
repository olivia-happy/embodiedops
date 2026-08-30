"""Minimal, prompt-safe trace persistence for generation attempts."""

from __future__ import annotations

from signalforge.core.models import TraceRecord, TraceStage
from signalforge.db.connection import Database
from signalforge.db.repositories import EntityType, ValidationStatus, save_trace


def estimate_tokens(*texts: str) -> int:
    """Return a conservative local estimate without storing request contents."""

    return sum((len(text) + 3) // 4 for text in texts if text)


def persist_generation_trace(
    db: Database | None,
    *,
    entity_id: str,
    dataset_version_id: str,
    prompt_version: str,
    evidence_ids: list[str],
    validation_status: ValidationStatus,
    latency_ms: int,
    token_estimate: int,
    model_name: str | None = None,
    provider: str | None = None,
    stage: TraceStage = "generation",
    retry_count: int = 0,
    entity_type: EntityType = "insight",
) -> TraceRecord | None:
    """Persist metadata only; prompts, responses, and credentials are excluded."""

    if db is None:
        return None
    return save_trace(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        dataset_version_id=dataset_version_id,
        prompt_version=prompt_version,
        evidence_ids=evidence_ids,
        validation_status=validation_status,
        latency_ms=latency_ms,
        token_estimate=token_estimate,
        model_name=model_name,
        provider=provider,
        stage=stage,
        retry_count=retry_count,
    )


def persist_memo_generation_trace(
    db: Database | None,
    *,
    memo_id: str,
    dataset_version_id: str,
    prompt_version: str,
    evidence_ids: list[str],
    validation_status: ValidationStatus,
    latency_ms: int,
    token_estimate: int,
    model_name: str | None,
    provider: str | None,
    stage: TraceStage,
    retry_count: int,
) -> TraceRecord | None:
    """Persist one memo-attempt event using only audit-safe metadata.

    ``prompt_version`` is stage-specific (for example, grouping or final memo)
    so the current trace schema can distinguish attempts without storing the
    actual prompt or response.  Retry occurrence is represented by the
    existing ``retried`` validation status.
    """

    return persist_generation_trace(
        db,
        entity_id=memo_id,
        entity_type="memo",
        dataset_version_id=dataset_version_id,
        prompt_version=prompt_version,
        evidence_ids=evidence_ids,
        validation_status=validation_status,
        latency_ms=latency_ms,
        token_estimate=token_estimate,
        model_name=model_name,
        provider=provider,
        stage=stage,
        retry_count=retry_count,
    )
