"""Versioned, read-only-safe API for the EmbodiedOps episode surface."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from signalforge.api.deps import get_database, get_settings
from signalforge.core.config import Settings
from signalforge.db.connection import Database
from signalforge.db.repositories import (
    create_embodied_diagnosis_job,
    get_dataset_version,
    get_embodied_diagnosis,
    get_embodied_episode,
    get_embodied_experiment,
    get_latest_dataset_version,
    insert_dataset_version,
    insert_embodied_episodes,
    list_dataset_versions,
    list_embodied_task_metrics,
    save_embodied_experiment,
)
from signalforge.embodied.diagnosis_models import ValidatedDiagnosis
from signalforge.embodied.experiment import build_reproduction_experiment
from signalforge.embodied.models import Episode
from signalforge.embodied.phase_analysis import extract_episode_features, segment_episode

router = APIRouter(prefix="/embodied", tags=["embodied"])


def _dataset_catalog_entry(db: Database, dataset_version_id: str) -> dict[str, object]:
    """Describe one dataset without inferring real-world provenance."""

    version = get_dataset_version(db, dataset_version_id)
    if version is None:
        raise ValueError("catalog version must exist")
    tasks = list_embodied_task_metrics(db, dataset_version_id)
    first_episode = db.execute(
        """
        SELECT episode_id FROM embodied_episodes
        WHERE dataset_version_id = ? ORDER BY episode_id LIMIT 1
        """,
        (dataset_version_id,),
    ).fetchone()
    source_name = version.source_name
    is_robomimic = source_name == "robomimic_v0.1_lift_proficient_human_low_dim"
    return {
        "dataset_version_id": version.id,
        "source_name": source_name,
        "source_url": str(version.source_url) if version.source_url is not None else None,
        "file_hash": version.file_hash,
        "episode_count": version.row_count,
        "real_robot_data": False,
        "data_type": "public_simulation" if is_robomimic else "synthetic_simulation",
        "purpose": "success_replay_baseline" if is_robomimic else "failure_diagnosis_regression",
        "default_episode_id": str(first_episode[0]) if first_episode else None,
        "task_count": len(tasks),
        "success_rate": tasks[0]["metrics"]["success_rate"] if tasks else None,
        "interpretation": (
            "公开仿真成功轨迹，仅用于外部数据导入与回放基线；不能证明失败诊断、真实机器人或 ROI。"
            if is_robomimic
            else (
                "合成失败回归集，用于验证阶段、证据约束与 fail-closed 诊断规则；"
                "不能代表真实机器人表现。"
            )
        ),
    }


class EpisodeImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version_id: str = Field(min_length=1)
    source_name: str = Field(default="synthetic_tabletop_fixture", min_length=1)
    file_hash: str | None = None
    source_url: str | None = None
    manifest: dict[str, object] = Field(default_factory=dict)
    episodes: list[Episode] | None = None
    jsonl: str | None = None


class DiagnosisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)


@router.get("/datasets")
def get_datasets(db: Database = Depends(get_database)) -> dict[str, object]:
    """List separately interpretable EmbodiedOps versions for the UI selector."""

    versions = list_dataset_versions(db)
    return {"datasets": [_dataset_catalog_entry(db, version.id) for version in versions]}


def _read_only(settings: Settings) -> None:
    if settings.demo_read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "DEMO_READ_ONLY", "message": "Embodied demo is read-only."},
        )


def _parse_episodes(payload: EpisodeImportRequest) -> list[Episode]:
    if payload.episodes is not None:
        return payload.episodes
    if payload.jsonl is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "EPISODES_REQUIRED", "message": "episodes or jsonl is required"},
        )
    episodes: list[Episode] = []
    for line in payload.jsonl.splitlines():
        if line.strip():
            try:
                episodes.append(Episode.model_validate(json.loads(line)))
            except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail={"code": "INVALID_EPISODE", "message": str(exc)},
                ) from exc
    return episodes


@router.post("/episodes/import", status_code=status.HTTP_201_CREATED)
def import_episodes(
    payload: EpisodeImportRequest,
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Import a local/demo manifest atomically; remote sources are rejected."""

    _read_only(settings)
    source = str(payload.manifest.get("source", payload.source_name))
    if (
        source.startswith("http")
        or (payload.source_url and payload.source_url.startswith(("http://", "https://")))
        or bool(payload.manifest.get("real_robot_data", False))
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "LOCAL_DEMO_ONLY",
                "message": "Only local/demo episode manifests are accepted.",
            },
        )
    episodes = _parse_episodes(payload)
    if not episodes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "EPISODES_REQUIRED", "message": "episodes must not be empty"},
        )
    if any(episode.dataset_version_id != payload.dataset_version_id for episode in episodes):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "DATASET_VERSION_MISMATCH",
                "message": "all episodes must match dataset_version_id",
            },
        )
    canonical = "\n".join(
        json.dumps(
            item.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        for item in episodes
    ).encode()
    file_hash = payload.file_hash or hashlib.sha256(canonical).hexdigest()
    version = get_dataset_version(db, payload.dataset_version_id)
    if (
        version is not None
        and payload.file_hash is not None
        and version.file_hash != payload.file_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DATASET_VERSION_IMMUTABLE",
                "message": "dataset version metadata cannot be overwritten",
            },
        )
    if version is None:
        insert_dataset_version(
            db,
            payload.dataset_version_id,
            source,
            file_hash,
            len(episodes),
            source_url=payload.source_url,
        )
    inserted = insert_embodied_episodes(db, episodes)
    return {
        "dataset_version_id": payload.dataset_version_id,
        "imported_count": inserted,
        "episode_count": len(episodes),
        "idempotent": inserted == 0,
        "file_hash": file_hash,
        "source": source,
    }


@router.get("/tasks")
def get_tasks(
    dataset_version_id: str = Query(min_length=1),
    db: Database = Depends(get_database),
) -> dict[str, object]:
    if get_dataset_version(db, dataset_version_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DATASET_NOT_FOUND", "message": "Dataset version not found."},
        )
    return {
        "dataset_version_id": dataset_version_id,
        "tasks": list_embodied_task_metrics(db, dataset_version_id),
    }


@router.get("/episodes/{episode_id}")
def get_episode(
    episode_id: str,
    dataset_version_id: str | None = Query(default=None, min_length=1),
    db: Database = Depends(get_database),
) -> dict[str, object]:
    if dataset_version_id is None:
        latest = get_latest_dataset_version(db)
        if latest is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "DATASET_NOT_FOUND", "message": "Dataset version not found."},
            )
        dataset_version_id = latest.id
    episode = get_embodied_episode(db, dataset_version_id, episode_id)
    if episode is None:
        raise HTTPException(
            status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": "Episode not found."}
        )
    phases = segment_episode(episode)
    features = extract_episode_features(episode, phases)
    return {
        "episode": episode.model_dump(mode="json"),
        "dataset_version_id": dataset_version_id,
        "phases": [phase.model_dump(mode="json") for phase in phases],
        "features": features.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in episode.events],
        "outcome": episode.outcome.model_dump(mode="json"),
    }


@router.post("/diagnoses", status_code=status.HTTP_202_ACCEPTED)
def create_diagnosis(
    payload: DiagnosisRequest,
    db: Database = Depends(get_database),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    _read_only(settings)
    if get_embodied_episode(db, payload.dataset_version_id, payload.episode_id) is None:
        raise HTTPException(
            status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": "Episode not found."}
        )
    try:
        job = create_embodied_diagnosis_job(
            db,
            dataset_version_id=payload.dataset_version_id,
            episode_id=payload.episode_id,
            diagnosis_id=f"diag-{uuid4()}",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail={"code": "EPISODE_NOT_FOUND", "message": str(exc)}
        ) from exc
    return job


@router.get("/diagnoses/{diagnosis_id}")
def get_diagnosis(diagnosis_id: str, db: Database = Depends(get_database)) -> dict[str, object]:
    diagnosis = get_embodied_diagnosis(db, diagnosis_id)
    if diagnosis is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIAGNOSIS_NOT_FOUND", "message": "Diagnosis not found."},
        )
    diagnosis["created_at"] = (
        diagnosis["created_at"].isoformat()
        if isinstance(diagnosis["created_at"], datetime)
        else diagnosis["created_at"]
    )
    diagnosis["updated_at"] = (
        diagnosis["updated_at"].isoformat()
        if isinstance(diagnosis["updated_at"], datetime)
        else diagnosis["updated_at"]
    )
    trace = diagnosis.get("trace")
    diagnosis["trace_ids"] = (
        [trace["trace_id"]] if isinstance(trace, dict) and trace.get("trace_id") else []
    )
    return diagnosis


@router.get("/experiments/{diagnosis_id}")
def get_experiment(diagnosis_id: str, db: Database = Depends(get_database)) -> dict[str, object]:
    existing = get_embodied_experiment(db, diagnosis_id)
    if existing is not None:
        return existing
    diagnosis = get_embodied_diagnosis(db, diagnosis_id)
    if diagnosis is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "DIAGNOSIS_NOT_FOUND", "message": "Diagnosis not found."},
        )
    if diagnosis["status"] != "completed" or diagnosis["payload"] is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "DIAGNOSIS_NOT_COMPLETED", "message": "Diagnosis has not completed."},
        )
    try:
        validated = ValidatedDiagnosis.model_validate(diagnosis["payload"])
        experiment = build_reproduction_experiment(validated)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(
            status_code=409, detail={"code": "EXPERIMENT_NOT_AVAILABLE", "message": str(exc)}
        ) from exc
    return save_embodied_experiment(db, experiment, diagnosis_id=diagnosis_id)
