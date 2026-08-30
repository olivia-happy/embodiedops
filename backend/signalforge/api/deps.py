"""FastAPI dependencies and snapshot helpers."""

from fastapi import HTTPException, Request, status

from signalforge.core.config import Settings
from signalforge.core.models import DatasetVersion
from signalforge.db.connection import Database
from signalforge.db.repositories import get_dataset_version, get_latest_dataset_version
from signalforge.services.memo_jobs import MemoJobManager


def get_database(request: Request) -> Database:
    """Retrieve the application-owned database; routes never construct one per request."""

    return request.app.state.database


def get_settings(request: Request) -> Settings:
    """Retrieve application configuration without exposing secret fields."""

    return request.app.state.settings


def get_memo_job_manager(request: Request) -> MemoJobManager:
    """Return the app-owned manager instead of constructing workers per request."""

    return request.app.state.memo_job_manager


def resolve_dataset_version(
    db: Database, dataset_version_id: str | None
) -> DatasetVersion:
    """Resolve an explicit version or the latest imported snapshot, otherwise return 404."""

    version = (
        get_dataset_version(db, dataset_version_id)
        if dataset_version_id is not None
        else get_latest_dataset_version(db)
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "DATASET_NOT_FOUND", "message": "未找到可用的数据版本。"},
        )
    return version
