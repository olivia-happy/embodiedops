"""Persistence contracts for versioned embodied episodes and diagnoses."""

from datetime import datetime

import pytest

from signalforge.db.connection import Database
from signalforge.db.repositories import (
    create_embodied_diagnosis_job,
    get_embodied_diagnosis,
    get_embodied_episode,
    insert_embodied_episodes,
    list_embodied_task_metrics,
)
from signalforge.embodied.simulator import generate_demo_episodes


def _db(tmp_path):
    database = Database(tmp_path / "embodied.duckdb")
    database.apply_schema()
    database.execute(
        "INSERT INTO dataset_versions VALUES (?, ?, ?, ?, ?, ?)",
        ("emb-v1", "synthetic", None, "sha256:emb-v1", 3, datetime(2026, 8, 11, 9, 0)),
    )
    return database


def test_episode_import_is_version_scoped_and_duplicate_idempotent(tmp_path) -> None:
    db = _db(tmp_path)
    episodes = generate_demo_episodes(count=3, seed=7, dataset_version_id="emb-v1")

    assert insert_embodied_episodes(db, episodes) == 3
    assert insert_embodied_episodes(db, episodes) == 0
    stored = get_embodied_episode(db, "emb-v1", episodes[0].episode_id)
    assert stored is not None
    assert stored.dataset_version_id == "emb-v1"
    assert stored.observations[0].t == 0
    assert list_embodied_task_metrics(db, "emb-v1")[0]["task_id"] == "tabletop_pick_place"


def test_episode_rows_are_immutable(tmp_path) -> None:
    db = _db(tmp_path)
    episode = generate_demo_episodes(count=1, seed=7, dataset_version_id="emb-v1")[0]
    insert_embodied_episodes(db, [episode])
    changed = episode.model_copy(update={"instruction": "overwrite"})
    with pytest.raises(ValueError, match="immutable"):
        insert_embodied_episodes(db, [changed])


def test_failed_diagnosis_has_no_partial_payload_and_keeps_trace_fields(tmp_path) -> None:
    db = _db(tmp_path)
    episode = generate_demo_episodes(count=1, seed=7, dataset_version_id="emb-v1")[0]
    insert_embodied_episodes(db, [episode])
    job = create_embodied_diagnosis_job(
        db,
        dataset_version_id="emb-v1",
        episode_id=episode.episode_id,
        diagnosis_id="diag-1",
    )
    assert job["status"] == "queued"
    stored = get_embodied_diagnosis(db, "diag-1")
    assert stored is not None
    assert stored["payload"] is None
    assert stored["trace"]["provider"] == "none"
    assert stored["trace"]["stage"] == "queued"
    assert stored["trace"]["retry_count"] == 0
    assert stored["trace"]["validation_code"] == "QUEUED"
