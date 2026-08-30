"""Tests for reproducible, validation-first snapshot imports."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from signalforge.db.connection import Database
from signalforge.etl.load import ImportValidationError, SourceMetadata, load_snapshot
from signalforge.etl.normalize import normalize_review


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _metadata(manifest_dir: Path) -> SourceMetadata:
    return SourceMetadata(
        slug="fixture",
        source_name="Fixture reviews",
        source_url="https://example.com/source",
        license_name="CC0-1.0",
        citation="Fixture citation",
        manifest_dir=manifest_dir,
    )


def _valid_events(path: Path) -> None:
    _write_csv(
        path,
        [
            "event_id",
            "source_url",
            "published_on",
            "excerpt",
            "event_type",
            "industry",
            "evidence_quality",
        ],
        [
            {
                "event_id": "e1",
                "source_url": "https://example.com/event",
                "published_on": "2024-01-02",
                "excerpt": "A reviewed public event.",
                "event_type": "policy",
                "industry": "semiconductor",
                "evidence_quality": "90",
            }
        ],
    )


def test_normalize_review_redacts_phone_number() -> None:
    review = normalize_review(
        {"review_id": "1", "review": "请联系 13812345678", "rating": "1"}
    )

    assert review.content == "请联系 [PHONE]"
    assert review.redacted is True
    assert review.rating == 1


def test_load_snapshot_is_idempotent_and_writes_a_manifest(tmp_path: Path) -> None:
    reviews_path = tmp_path / "reviews.csv"
    events_path = tmp_path / "events.csv"
    _write_csv(
        reviews_path,
        ["review_id", "review", "rating", "aspect", "sentiment"],
        [
            {
                "review_id": "1",
                "review": "服务响应慢，请联系 13812345678",
                "rating": "1",
                "aspect": "service",
                "sentiment": "negative",
            }
        ],
    )
    _valid_events(events_path)
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()

    metadata = _metadata(tmp_path / "manifests")
    version = load_snapshot(db, reviews_path, events_path, metadata)
    second_load = load_snapshot(db, reviews_path, events_path, metadata)

    assert second_load == version
    assert db.execute("SELECT COUNT(*) FROM dataset_versions").fetchone() == (1,)
    assert db.execute("SELECT content, redacted FROM reviews").fetchone() == (
        "服务响应慢，请联系 [PHONE]",
        True,
    )
    manifest = json.loads((metadata.manifest_dir / f"{version.id}.json").read_text("utf-8"))
    assert manifest["version_id"] == version.id
    assert manifest["files"]["reviews"]["row_count"] == 1
    assert manifest["column_mapping"]["reviews"]["content"] == "review"


def test_invalid_input_keeps_previously_imported_versions(tmp_path: Path) -> None:
    reviews_path = tmp_path / "reviews.csv"
    events_path = tmp_path / "events.csv"
    _write_csv(
        reviews_path,
        ["review_id", "review", "rating"],
        [{"review_id": "1", "review": "体验不错", "rating": "5"}],
    )
    _valid_events(events_path)
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    metadata = _metadata(tmp_path / "manifests")
    load_snapshot(db, reviews_path, events_path, metadata)

    _write_csv(reviews_path, ["review_id", "review"], [{"review_id": "2", "review": "缺少评分"}])

    with pytest.raises(ImportValidationError) as error:
        load_snapshot(db, reviews_path, events_path, metadata)

    assert error.value.errors[0].field == "rating"
    assert db.execute("SELECT COUNT(*) FROM dataset_versions").fetchone() == (1,)


def test_loader_supports_explicit_column_mapping(tmp_path: Path) -> None:
    reviews_path = tmp_path / "reviews.csv"
    events_path = tmp_path / "events.csv"
    _write_csv(
        reviews_path,
        ["external_id", "body", "stars"],
        [{"external_id": "A/1", "body": "餐品温度合适", "stars": "5"}],
    )
    _valid_events(events_path)
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    metadata = SourceMetadata(
        slug="mapped",
        source_name="Mapped fixture",
        source_url="https://example.com/mapped",
        license_name="CC0-1.0",
        citation="Fixture citation",
        manifest_dir=tmp_path / "manifests",
        review_column_mapping={"review_id": "external_id", "review": "body", "rating": "stars"},
    )

    load_snapshot(db, reviews_path, events_path, metadata)

    stored = db.execute("SELECT id, content FROM reviews").fetchone()
    assert stored == ("mapped-a-1", "餐品温度合适")
