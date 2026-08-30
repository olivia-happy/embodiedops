"""Safe generation-trace read route; it never exposes prompts, responses, or credentials."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from signalforge.api.deps import get_database
from signalforge.api.schemas import PaginationResponse, TracesResponse
from signalforge.core.models import TraceRecord
from signalforge.db.connection import Database

router = APIRouter(tags=["traces"])


@router.get("/traces/{entity_id}", response_model=TracesResponse)
def get_traces(
    entity_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_database),
) -> TracesResponse:
    """Read the minimal audit trail for one entity with bounded pagination."""

    total_row = db.execute(
        "SELECT COUNT(*) FROM traces WHERE entity_id = ?", (entity_id,)
    ).fetchone()
    total = int(total_row[0])
    rows = db.execute(
        """
        SELECT id, entity_type, entity_id, dataset_version_id, prompt_version, model_name,
               provider, stage, retry_count, evidence_ids, validation_status, latency_ms,
               token_estimate, created_at
        FROM traces WHERE entity_id = ?
        ORDER BY created_at ASC, id ASC LIMIT ? OFFSET ?
        """,
        (entity_id, page_size, (page - 1) * page_size),
    ).fetchall()
    traces = [
        TraceRecord(
            id=str(row[0]),
            entity_type=str(row[1]),
            entity_id=str(row[2]),
            dataset_version_id=str(row[3]),
            prompt_version=str(row[4]),
            model_name=str(row[5]) if row[5] is not None else None,
            provider=str(row[6]) if row[6] is not None else None,
            stage=str(row[7]) if row[7] is not None else "generation",
            retry_count=int(row[8]) if row[8] is not None else 0,
            evidence_ids=json.loads(str(row[9])),
            validation_status=str(row[10]),
            latency_ms=int(row[11]),
            token_estimate=int(row[12]),
            created_at=row[13],
        )
        for row in rows
    ]
    return TracesResponse(
        entity_id=entity_id,
        traces=traces,
        pagination=PaginationResponse(page=page, page_size=page_size, total=total),
    )
