"""Insight listing and evidence-safe generation routes."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from signalforge.api.deps import get_database, get_settings, resolve_dataset_version
from signalforge.api.routers.overview import dataset_metadata
from signalforge.api.schemas import (
    GeneratedInsightResponse,
    GenerateInsightBody,
    InsightsResponse,
    PaginationResponse,
    Sentiment,
    TopicMetricResponse,
)
from signalforge.core.config import Settings
from signalforge.core.errors import EvidenceInsufficientError
from signalforge.core.models import Evidence, Insight
from signalforge.db.connection import Database
from signalforge.db.repositories import get_evidence, list_insights, save_insight
from signalforge.services.analytics import TopicFilters, list_topic_metrics
from signalforge.services.generation import GenerateInsightRequest, Refusal, generate_insight
from signalforge.services.retrieval import retrieve_evidence

router = APIRouter(tags=["insights"])


@router.get("/evidence", response_model=list[Evidence])
def read_evidence(
    dataset_version_id: str = Query(min_length=1, max_length=128),
    evidence_id: list[str] = Query(min_length=1, max_length=8),
    db: Database = Depends(get_database),
) -> list[Evidence]:
    """Return original review evidence only from the explicitly selected snapshot."""

    resolve_dataset_version(db, dataset_version_id)
    return [
        Evidence(dataset_version_id=dataset_version_id, source_type="review", **record)
        for record in get_evidence(db, dataset_version_id, evidence_id)
    ]


@router.get("/insights", response_model=InsightsResponse)
def get_insights(
    dataset_version_id: str | None = Query(default=None, min_length=1, max_length=128),
    aspect: list[str] | None = Query(default=None, max_length=20),
    sentiment: Sentiment | None = Query(default=None),
    minimum_rating: int | None = Query(default=None, ge=1, le=5),
    maximum_rating: int | None = Query(default=None, ge=1, le=5),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> InsightsResponse:
    """Return SQL-derived topic metrics and previously validated generated insights."""

    version = resolve_dataset_version(db, dataset_version_id)
    filters = TopicFilters(
        aspects=tuple(aspect) if aspect else None,
        sentiment=sentiment,
        minimum_rating=minimum_rating,
        maximum_rating=maximum_rating,
    )
    metrics = list_topic_metrics(db, version.id, filters)
    total_row = db.execute(
        "SELECT COUNT(*) FROM insights WHERE dataset_version_id = ?", (version.id,)
    ).fetchone()
    total = int(total_row[0])
    return InsightsResponse(
        active_dataset=dataset_metadata(version, settings),
        metrics=[
            TopicMetricResponse(
                aspect=metric.aspect,
                review_count=metric.review_count,
                negative_count=metric.negative_count,
                negative_rate=metric.negative_rate,
                average_rating=metric.average_rating,
                severity=metric.severity,
                affected=metric.affected,
                evidence=metric.evidence,
                scoreable=metric.scoreable,
                missing_fields=metric.missing_fields,
            )
            for metric in metrics
        ],
        insights=list_insights(db, version.id, limit=page_size, offset=(page - 1) * page_size),
        pagination=PaginationResponse(page=page, page_size=page_size, total=total),
    )


@router.post("/insights/generate", response_model=GeneratedInsightResponse, status_code=201)
def generate(
    body: GenerateInsightBody,
    db: Database = Depends(get_database),
) -> GeneratedInsightResponse:
    """Generate and persist an insight only when two active-snapshot evidence rows support it."""

    resolve_dataset_version(db, body.dataset_version_id)
    evidence = retrieve_evidence(db, body.dataset_version_id, body.query, limit=body.limit)
    insight_id = str(uuid4())
    result = generate_insight(
        GenerateInsightRequest(
            dataset_version_id=body.dataset_version_id,
            query=body.query,
            evidence=evidence,
            db=db,
            entity_id=insight_id,
        )
    )
    if isinstance(result, Refusal):
        raise EvidenceInsufficientError(
            result.message,
            evidence_count=result.evidence_count,
            required_count=result.required_count,
        )
    insight = Insight(
        id=insight_id,
        dataset_version_id=body.dataset_version_id,
        title=f"“{body.query}”相关用户洞察",
        claim=result.claim,
        evidence_ids=result.evidence_ids,
        confidence=result.confidence,
        unknowns=result.unknowns,
        recommended_action=result.recommended_action,
        generation_method="fallback",
        status="validated",
    )
    save_insight(db, insight)
    return GeneratedInsightResponse(insight=insight, evidence_count=len(evidence))
