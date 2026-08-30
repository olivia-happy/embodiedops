"""Authority-source risk radar route."""

from fastapi import APIRouter, Depends, Query

from signalforge.api.deps import get_database, get_settings, resolve_dataset_version
from signalforge.api.routers.overview import dataset_metadata, risk_cards
from signalforge.api.schemas import EventType, PaginationResponse, RisksResponse
from signalforge.core.config import Settings
from signalforge.db.connection import Database

router = APIRouter(tags=["risks"])


@router.get("/risks", response_model=RisksResponse)
def get_risks(
    dataset_version_id: str | None = Query(default=None, min_length=1, max_length=128),
    event_type: EventType | None = Query(default=None),
    industry: str | None = Query(default=None, min_length=1, max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> RisksResponse:
    """Return only manually imported, version-scoped authority event snapshots."""

    version = resolve_dataset_version(db, dataset_version_id)
    clauses = ["dataset_version_id = ?"]
    params: list[object] = [version.id]
    if event_type is not None:
        clauses.append("event_type = ?")
        params.append(event_type)
    if industry is not None:
        clauses.append("industry = ?")
        params.append(industry)
    total = int(
        db.execute(
            f"SELECT COUNT(*) FROM market_events WHERE {' AND '.join(clauses)}", tuple(params)
        ).fetchone()[0]
    )
    return RisksResponse(
        active_dataset=dataset_metadata(version, settings),
        risks=risk_cards(
            db,
            version.id,
            limit=page_size,
            offset=(page - 1) * page_size,
            event_type=event_type,
            industry=industry,
        ),
        pagination=PaginationResponse(page=page, page_size=page_size, total=total),
    )
