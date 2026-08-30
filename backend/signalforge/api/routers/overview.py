"""Decision-overview route and deterministic response assembly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query

from signalforge.api.deps import get_database, get_settings, resolve_dataset_version
from signalforge.api.schemas import (
    DatasetMetadataResponse,
    OpportunityCardResponse,
    OverviewResponse,
    RiskCardResponse,
    SummaryMetricsResponse,
)
from signalforge.core.config import Settings
from signalforge.core.models import DatasetVersion
from signalforge.db.connection import Database
from signalforge.services.analytics import list_topic_metrics
from signalforge.services.scoring import (
    OpportunityInputs,
    RiskInputs,
    score_opportunity,
    score_risk,
)

router = APIRouter(tags=["overview"])


def dataset_metadata(version: DatasetVersion, settings: Settings) -> DatasetMetadataResponse:
    """Build display metadata and a non-blocking stale-data warning flag."""

    # The database schema requires this field; retain a boundary guard anyway.
    if version.imported_at is None:
        raise ValueError("dataset version is missing imported_at")
    imported_at = version.imported_at
    if imported_at.tzinfo is None:
        imported_at = imported_at.replace(tzinfo=UTC)
    is_stale = imported_at < datetime.now(UTC) - timedelta(days=settings.demo_stale_after_days)
    return DatasetMetadataResponse(
        id=version.id,
        source_name=version.source_name,
        source_url=version.source_url,
        file_hash=version.file_hash,
        row_count=version.row_count,
        imported_at=imported_at,
        is_stale=is_stale,
    )


def _evidence_ids_for_aspect(db: Database, version_id: str, aspect: str) -> list[str]:
    rows = db.execute(
        """
        SELECT id FROM reviews
        WHERE dataset_version_id = ? AND aspect = ?
        ORDER BY CASE WHEN sentiment = 'negative' THEN 0 ELSE 1 END, id ASC
        LIMIT 8
        """,
        (version_id, aspect),
    ).fetchall()
    return [str(row[0]) for row in rows]


def opportunity_cards(
    db: Database, version_id: str, business_fit: float
) -> list[OpportunityCardResponse]:
    """Compose up to three score-explainable cards from SQL-backed metrics."""

    cards: list[OpportunityCardResponse] = []
    for metric in list_topic_metrics(db, version_id):
        score = score_opportunity(
            OpportunityInputs(
                affected=metric.affected,
                negativity=metric.negative_rate,
                severity=metric.severity,
                business_fit=business_fit,
                evidence=metric.evidence,
            )
        )
        evidence_ids = _evidence_ids_for_aspect(db, version_id, metric.aspect)
        cards.append(
            OpportunityCardResponse(
                id=f"opportunity:{version_id}:{metric.aspect}",
                title=f"{metric.aspect} 体验机会",
                dataset_version_id=version_id,
                aspect=metric.aspect,
                review_count=metric.review_count,
                negative_count=metric.negative_count,
                negative_rate=metric.negative_rate,
                score=score.total,
                scoreable=score.scoreable,
                missing_fields=sorted(set(metric.missing_fields + score.missing_fields)),
                contributions=score.contributions,
                evidence_ids=evidence_ids,
                evidence_count=len(evidence_ids),
            )
        )
    return sorted(
        cards,
        key=lambda card: (
            not card.scoreable,
            -(card.score or -1),
            -card.negative_count,
            card.aspect,
        ),
    )[:3]


def _risk_inputs(event_type: str, published_on: object, evidence_quality: int) -> RiskInputs:
    """Map transparent public-event attributes to fixed risk inputs (all 0-100)."""

    impact_by_type = {"policy": 80.0, "market": 70.0, "talent": 45.0}
    likelihood_by_type = {"policy": 75.0, "market": 60.0, "talent": 45.0}
    event_date = published_on if hasattr(published_on, "year") else datetime.now(UTC).date()
    age_days = max(0, (datetime.now(UTC).date() - event_date).days)
    urgency = round(max(10.0, 100.0 - min(age_days, 365) / 365 * 90), 1)
    return RiskInputs(
        impact=impact_by_type.get(event_type, 50.0),
        urgency=urgency,
        likelihood=likelihood_by_type.get(event_type, 50.0),
        evidence_quality=float(evidence_quality),
    )


def risk_cards(
    db: Database,
    version_id: str,
    *,
    limit: int,
    offset: int = 0,
    event_type: str | None = None,
    industry: str | None = None,
) -> list[RiskCardResponse]:
    """Return authority-source events with deterministic risk scores; no model is called."""

    clauses = ["dataset_version_id = ?"]
    params: list[object] = [version_id]
    if event_type is not None:
        clauses.append("event_type = ?")
        params.append(event_type)
    if industry is not None:
        clauses.append("industry = ?")
        params.append(industry)
    rows = db.execute(
        f"""
        SELECT id, source_url, published_on, excerpt, event_type, industry, evidence_quality
        FROM market_events WHERE {' AND '.join(clauses)}
        ORDER BY published_on DESC, id ASC LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    cards: list[RiskCardResponse] = []
    for event_id, source_url, published_on, excerpt, kind, event_industry, quality in rows:
        score = score_risk(_risk_inputs(str(kind), published_on, int(quality)))
        cards.append(
            RiskCardResponse(
                id=str(event_id),
                dataset_version_id=version_id,
                source_url=str(source_url),
                published_on=published_on,
                excerpt=str(excerpt),
                event_type=str(kind),
                industry=str(event_industry),
                evidence_quality=int(quality),
                score=score.total or 0,
                contributions=score.contributions,
                mitigation_action="核对业务影响范围并指定复核负责人。",
                owner="待分配",
                status="needs_review" if (score.total or 0) >= 65 else "monitoring",
            )
        )
    return cards


@router.get("/overview", response_model=OverviewResponse)
def get_overview(
    dataset_version_id: str | None = Query(default=None, min_length=1, max_length=128),
    business_fit: float = Query(default=70, ge=0, le=100),
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> OverviewResponse:
    """Expose a compact evidence-backed decision overview for one active snapshot."""

    version = resolve_dataset_version(db, dataset_version_id)
    summary = db.execute(
        """
        SELECT COUNT(*), COUNT(*) FILTER (WHERE sentiment = 'negative'),
               COUNT(DISTINCT COALESCE(NULLIF(aspect, ''), 'unknown'))
        FROM reviews WHERE dataset_version_id = ?
        """,
        (version.id,),
    ).fetchone()
    event_count = db.execute(
        "SELECT COUNT(*) FROM market_events WHERE dataset_version_id = ?", (version.id,)
    ).fetchone()[0]
    review_count, negative_count, topic_count = (int(value) for value in summary)
    return OverviewResponse(
        active_dataset=dataset_metadata(version, settings),
        summary_metrics=SummaryMetricsResponse(
            review_count=review_count,
            negative_review_count=negative_count,
            negative_review_rate=round(negative_count / review_count * 100, 1)
            if review_count
            else None,
            topic_count=topic_count,
            market_event_count=int(event_count),
        ),
        opportunities=opportunity_cards(db, version.id, business_fit),
        risks=risk_cards(db, version.id, limit=2),
    )
