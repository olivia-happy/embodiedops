"""Decision and human-feedback routes with strict evidence validation."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status

from signalforge.api.deps import get_database, resolve_dataset_version
from signalforge.api.schemas import CreateDecisionBody, FeedbackBody, FeedbackResponse
from signalforge.core.models import DecisionCard
from signalforge.db.connection import Database
from signalforge.db.repositories import get_evidence, save_decision_card, save_feedback
from signalforge.services.analytics import TopicFilters, list_topic_metrics
from signalforge.services.scoring import score_opportunity

router = APIRouter(tags=["decisions"])


def _invalid_evidence(message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": "INVALID_EVIDENCE", "message": message},
    )


@router.post("/decisions", response_model=DecisionCard, status_code=status.HTTP_201_CREATED)
def create_decision(
    body: CreateDecisionBody, db: Database = Depends(get_database)
) -> DecisionCard:
    """Persist an action card after evidence is checked against its exact snapshot."""

    resolve_dataset_version(db, body.dataset_version_id)
    evidence = get_evidence(db, body.dataset_version_id, body.evidence_ids)
    if len(evidence) != len(body.evidence_ids):
        raise _invalid_evidence("存在不属于当前数据版本的证据 ID。")
    aspects = {str(item["aspect"] or "unknown") for item in evidence}
    score = None
    if len(aspects) == 1:
        metric = next(
            iter(
                list_topic_metrics(
                    db, body.dataset_version_id, TopicFilters(aspects=(next(iter(aspects)),))
                )
            ),
            None,
        )
        if metric is not None:
            score = score_opportunity(metric.to_opportunity_inputs(business_fit=body.business_fit))
    card = DecisionCard(
        id=str(uuid4()),
        dataset_version_id=body.dataset_version_id,
        title=body.title,
        evidence_ids=body.evidence_ids,
        problem_statement=body.problem_statement,
        hypothesis=body.hypothesis,
        primary_metric=body.primary_metric,
        guardrail_metric=body.guardrail_metric,
        owner=body.owner,
        due_date=body.due_date,
        score=score.total if score else None,
        score_breakdown=score.contributions if score else {},
        status="planned",
    )
    return save_decision_card(db, card)


@router.post("/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
def create_feedback(body: FeedbackBody, db: Database = Depends(get_database)) -> FeedbackResponse:
    """Store one explicit reviewer decision; edited reviews must explain the change."""

    table_by_type = {"insight": "insights", "decision": "decision_cards", "risk": "market_events"}
    table = table_by_type[body.entity_type]
    exists = db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (body.entity_id,)).fetchone()
    if exists is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ENTITY_NOT_FOUND", "message": "未找到需要反馈的对象。"},
        )
    record = save_feedback(
        db,
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        decision=body.decision,
        reason=body.reason.strip() if body.reason else None,
    )
    return FeedbackResponse(**record)
