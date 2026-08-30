"""Contract tests for the public read-only interview demo mode."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_api import LocalAsgiClient

from signalforge.api.app import create_app
from signalforge.api.routers import decision_memos
from signalforge.core.config import Settings
from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version


def test_demo_read_only_setting_defaults_to_false_and_parses_environment(monkeypatch) -> None:
    assert Settings(_env_file=None).demo_read_only is False

    monkeypatch.setenv("DEMO_READ_ONLY", "true")
    assert Settings(_env_file=None).demo_read_only is True


def test_demo_read_only_blocks_generation_before_job_creation(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "demo-read-only.duckdb")
    database.apply_schema()
    insert_dataset_version(
        database,
        "v1",
        "fixture",
        "sha256:v1",
        0,
        source_url="https://example.com/source",
        imported_at=datetime.now(UTC).replace(tzinfo=None),
    )
    app = create_app(
        settings=Settings(_env_file=None, demo_read_only=True),
        database=database,
    )
    client = LocalAsgiClient(app)
    create_calls = 0
    start_calls = 0

    def fail_create(*args, **kwargs):
        nonlocal create_calls
        create_calls += 1
        raise AssertionError("read-only mode must reject before creating a job")

    def fail_start(*args, **kwargs):
        nonlocal start_calls
        start_calls += 1
        raise AssertionError("read-only mode must reject before starting a job")

    monkeypatch.setattr(decision_memos, "create_memo_job", fail_create)
    app.state.memo_job_manager.start = fail_start
    try:
        response = client.post(
            "/api/v1/decision-memo/generate",
            json={"dataset_version_id": "v1"},
        )
    finally:
        database.close()

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "DEMO_READ_ONLY",
        "message": "Generation is disabled in read-only demo mode.",
    }
    assert create_calls == 0
    assert start_calls == 0


def test_demo_read_only_bootstraps_embodied_fixture_from_data_mount(tmp_path) -> None:
    data_root = tmp_path / "data"
    embodied_root = data_root / "embodied"
    embodied_root.mkdir(parents=True)
    source_root = Path(__file__).parents[2] / "data" / "embodied"
    for filename in ("demo_episodes.jsonl", "demo_episode_manifest.json"):
        (embodied_root / filename).write_bytes((source_root / filename).read_bytes())

    database = Database(data_root / "signalforge.duckdb")
    database.apply_schema()
    app = create_app(
        settings=Settings(
            _env_file=None,
            database_path=str(database.path),
            demo_read_only=True,
        ),
        database=database,
    )
    client = LocalAsgiClient(app)
    try:
        response = client.get("/api/v1/embodied/tasks?dataset_version_id=embodied-demo-v1")
    finally:
        database.close()

    assert response.status_code == 200
    assert response.json()["tasks"][0]["metrics"]["episode_count"] == 30


def test_demo_read_only_fails_closed_when_configured_fixture_is_missing(tmp_path) -> None:
    database = Database(tmp_path / "data" / "signalforge.duckdb")
    try:
        with pytest.raises(ValueError, match="EMBODIED_DEMO_FIXTURE_MISSING"):
            create_app(
                settings=Settings(
                    _env_file=None,
                    database_path=str(database.path),
                    demo_read_only=True,
                ),
                database=database,
            )
    finally:
        database.close()


def test_demo_read_only_fails_closed_when_configured_fixture_hash_mismatches(tmp_path) -> None:
    data_root = tmp_path / "data"
    embodied_root = data_root / "embodied"
    embodied_root.mkdir(parents=True)
    source_root = Path(__file__).parents[2] / "data" / "embodied"
    for filename in ("demo_episodes.jsonl", "demo_episode_manifest.json"):
        (embodied_root / filename).write_bytes((source_root / filename).read_bytes())
    with (embodied_root / "demo_episodes.jsonl").open("ab") as fixture:
        fixture.write(b"\n")

    database = Database(data_root / "signalforge.duckdb")
    try:
        with pytest.raises(ValueError, match="EMBODIED_DEMO_FIXTURE_HASH_MISMATCH"):
            create_app(
                settings=Settings(
                    _env_file=None,
                    database_path=str(database.path),
                    demo_read_only=True,
                ),
                database=database,
            )
    finally:
        database.close()


def test_demo_fixture_is_not_imported_when_read_only_mode_is_disabled(tmp_path) -> None:
    data_root = tmp_path / "data"
    embodied_root = data_root / "embodied"
    embodied_root.mkdir(parents=True)
    source_root = Path(__file__).parents[2] / "data" / "embodied"
    for filename in ("demo_episodes.jsonl", "demo_episode_manifest.json"):
        (embodied_root / filename).write_bytes((source_root / filename).read_bytes())

    database = Database(data_root / "signalforge.duckdb")
    app = create_app(
        settings=Settings(
            _env_file=None,
            database_path=str(database.path),
            demo_read_only=False,
        ),
        database=database,
    )
    client = LocalAsgiClient(app)
    try:
        response = client.get("/api/v1/embodied/tasks?dataset_version_id=embodied-demo-v1")
    finally:
        database.close()

    assert response.status_code == 404
