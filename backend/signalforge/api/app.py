"""SignalForge FastAPI application factory and safety-first exception envelopes."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from signalforge.api.routers import (
    decision_memos,
    decisions,
    embodied,
    insights,
    model_health,
    overview,
    risks,
    traces,
)
from signalforge.api.schemas import HealthResponse
from signalforge.core.config import Settings
from signalforge.core.errors import EvidenceInsufficientError
from signalforge.db.connection import Database
from signalforge.db.repositories import (
    fail_incomplete_memo_jobs,
    get_dataset_version,
    insert_dataset_version,
    insert_embodied_episodes,
)
from signalforge.embodied.models import Episode
from signalforge.embodied.robomimic_import import RobomimicImportManifest
from signalforge.embodied.robomimic_ingest import import_robomimic_artifact
from signalforge.services.local_model import LocalModelProvider
from signalforge.services.memo_generation import StructuredModelProvider
from signalforge.services.memo_jobs import MemoJobManager


def _bootstrap_embodied_demo(database: Database, settings: Settings) -> None:
    """Load the immutable local fixture for the read-only interview demo.

    The public demo must be useful immediately after ``docker compose up``;
    importing this checked-in fixture is setup, not a user write operation.
    Normal writable deployments still use the explicit import endpoint.
    """

    if not settings.demo_read_only:
        return
    configured_database_path = Path(settings.database_path).resolve()
    # Dependency-injected databases are used by API contract tests and local
    # tooling.  A fixture is only part of the configured application's startup
    # contract when that database is the configured store; otherwise silently
    # importing a neighbouring fixture would mutate an unrelated test store.
    if database.path.resolve() != configured_database_path:
        return

    data_root = configured_database_path.parent
    embodied_root = data_root / "embodied"
    episode_path = embodied_root / "demo_episodes.jsonl"
    manifest_path = embodied_root / "demo_episode_manifest.json"
    if not episode_path.is_file() or not manifest_path.is_file():
        raise ValueError("EMBODIED_DEMO_FIXTURE_MISSING")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        dataset_version_id = str(manifest["dataset_version_id"])
        expected_hash = str(manifest["sha256"])
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError) as error:
        raise ValueError("EMBODIED_DEMO_MANIFEST_INVALID") from error

    fixture_bytes = episode_path.read_bytes()
    actual_hash = hashlib.sha256(fixture_bytes).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("EMBODIED_DEMO_FIXTURE_HASH_MISMATCH")

    try:
        episodes = [
            Episode.model_validate_json(line)
            for line in fixture_bytes.decode("utf-8").splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("EMBODIED_DEMO_FIXTURE_INVALID") from error
    has_wrong_dataset_version = any(
        episode.dataset_version_id != dataset_version_id for episode in episodes
    )
    if not episodes or has_wrong_dataset_version:
        raise ValueError("EMBODIED_DEMO_FIXTURE_INVALID")
    if manifest.get("count") != len(episodes):
        raise ValueError("EMBODIED_DEMO_FIXTURE_INVALID")

    version = get_dataset_version(database, dataset_version_id)
    if version is not None and (
        version.file_hash != expected_hash or version.row_count != len(episodes)
    ):
        raise ValueError("EMBODIED_DEMO_DATASET_VERSION_CONFLICT")
    if version is None:
        insert_dataset_version(
            database,
            dataset_version_id,
            str(manifest.get("source", "synthetic_tabletop_fixture")),
            expected_hash,
            len(episodes),
        )
    insert_embodied_episodes(database, episodes)


def _bootstrap_public_robomimic(database: Database, settings: Settings) -> None:
    """Optionally import a local public HDF5 artifact; never download at startup."""

    if not settings.demo_read_only:
        return
    if database.path.resolve() != Path(settings.database_path).resolve():
        return
    raw_root = Path(settings.database_path).resolve().parent / "embodied" / "raw" / "robomimic"
    artifact = raw_root / "low_dim.hdf5"
    manifest_path = raw_root / "manifest.json"
    if not artifact.is_file() or not manifest_path.is_file():
        return
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        manifest = RobomimicImportManifest(
            dataset_version_id=str(payload["dataset_version_id"]),
            source_name=str(payload["source_name"]),
            source_url=str(payload["source_url"]),
            sha256=str(payload["sha256"]),
            data_type=str(payload["data_type"]),
            real_robot_data=bool(payload["real_robot_data"]),
            task_id=str(payload["task_id"]),
            episode_count=int(payload["episode_count"]),
        )
        import_robomimic_artifact(database, artifact=artifact, manifest=manifest)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("EMBODIED_PUBLIC_DATASET_INVALID") from error


def create_app(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    memo_provider: StructuredModelProvider | None = None,
    memo_job_manager: MemoJobManager | None = None,
) -> FastAPI:
    """Create an application that owns one initialized local DuckDB connection."""

    active_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        owns_database = not hasattr(application.state, "database")
        if owns_database:
            application.state.database = Database(Path(active_settings.database_path))
        application.state.database.apply_schema()
        _bootstrap_embodied_demo(application.state.database, active_settings)
        _bootstrap_public_robomimic(application.state.database, active_settings)
        if not hasattr(application.state, "memo_job_manager"):
            provider = memo_provider or LocalModelProvider(active_settings)
            application.state.memo_job_manager = MemoJobManager(
                application.state.database,
                provider,
            )
        fail_incomplete_memo_jobs(application.state.database)
        try:
            yield
        finally:
            await application.state.memo_job_manager.close()
            if owns_database:
                application.state.database.close()

    app = FastAPI(title="SignalForge API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )
    app.state.settings = active_settings
    if database is not None:
        database.apply_schema()
        _bootstrap_embodied_demo(database, active_settings)
        _bootstrap_public_robomimic(database, active_settings)
        app.state.database = database
        app.state.memo_job_manager = memo_job_manager or MemoJobManager(
            database,
            memo_provider or LocalModelProvider(active_settings),
        )
    elif memo_job_manager is not None:
        app.state.memo_job_manager = memo_job_manager

    @app.exception_handler(EvidenceInsufficientError)
    async def evidence_error(_: Request, exc: EvidenceInsufficientError) -> JSONResponse:
        detail: dict[str, object] = {"code": "INSUFFICIENT_EVIDENCE", "message": str(exc)}
        if exc.evidence_count is not None:
            detail["evidence_count"] = exc.evidence_count
        if exc.required_count is not None:
            detail["required_count"] = exc.required_count
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": detail},
        )

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    def healthz(request: Request) -> HealthResponse:
        """Report database readiness without leaking connection or filesystem details."""

        request.app.state.database.execute("SELECT 1").fetchone()
        return HealthResponse(status="ok", database="ready")

    prefix = "/api/v1"
    app.include_router(overview.router, prefix=prefix)
    app.include_router(insights.router, prefix=prefix)
    app.include_router(decisions.router, prefix=prefix)
    app.include_router(risks.router, prefix=prefix)
    app.include_router(traces.router, prefix=prefix)
    app.include_router(decision_memos.router, prefix=prefix)
    app.include_router(model_health.router)
    app.include_router(embodied.router, prefix=prefix)
    return app


app = create_app()
