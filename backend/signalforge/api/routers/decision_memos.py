"""Evidence-safe decision-memo reads and persisted asynchronous generation jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status

from signalforge.api.deps import (
    get_database,
    get_memo_job_manager,
    get_settings,
    resolve_dataset_version,
)
from signalforge.api.schemas import GenerateDecisionMemoBody
from signalforge.core.config import Settings
from signalforge.core.models import DecisionMemo, MemoGenerationJob
from signalforge.db.connection import Database
from signalforge.db.repositories import create_memo_job, get_decision_memo, get_memo_job
from signalforge.services.memo_jobs import MemoJobManager

router = APIRouter(tags=["decision-memo"])


def _not_found(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"code": code, "message": message},
    )


@router.get("/decision-memo", response_model=DecisionMemo)
def read_decision_memo(
    dataset_version_id: str | None = Query(default=None, min_length=1, max_length=128),
    db: Database = Depends(get_database),
) -> DecisionMemo:
    """Read the current memo for an explicit or latest available snapshot."""

    version = resolve_dataset_version(db, dataset_version_id)
    memo = get_decision_memo(db, version.id)
    if memo is None:
        raise _not_found("DECISION_MEMO_NOT_FOUND", "未找到该数据版本的有效决策备忘录。")
    return memo


@router.post(
    "/decision-memo/generate",
    response_model=MemoGenerationJob,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_decision_memo_job(
    body: GenerateDecisionMemoBody,
    db: Database = Depends(get_database),
    manager: MemoJobManager = Depends(get_memo_job_manager),
    settings: Settings = Depends(get_settings),
) -> MemoGenerationJob:
    """Create or reuse one active local-generation job without exposing model details."""

    if settings.demo_read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "DEMO_READ_ONLY",
                "message": "Generation is disabled in read-only demo mode.",
            },
        )

    version = resolve_dataset_version(db, body.dataset_version_id)
    timestamp = datetime.now(UTC).replace(tzinfo=None)
    requested = MemoGenerationJob(
        id=str(uuid4()),
        dataset_version_id=version.id,
        status="queued",
        created_at=timestamp,
        updated_at=timestamp,
    )
    job = create_memo_job(db, requested)
    manager.start(job.id)
    return job


@router.get("/decision-memo/jobs/{job_id}", response_model=MemoGenerationJob)
def read_decision_memo_job(
    job_id: str,
    db: Database = Depends(get_database),
) -> MemoGenerationJob:
    """Return the persisted stage and safe error code for one generation attempt."""

    job = get_memo_job(db, job_id)
    if job is None:
        raise _not_found("MEMO_JOB_NOT_FOUND", "未找到该决策备忘录生成任务。")
    return job
