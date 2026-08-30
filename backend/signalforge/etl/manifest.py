"""Immutable import-manifest construction and SHA-256 provenance helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path


def file_sha256(path: Path) -> str:
    """Return a streaming SHA-256 hash so large future snapshots remain supported."""

    digest = sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def utc_timestamp() -> str:
    """Return a portable, explicit UTC timestamp for JSON provenance records."""

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def build_manifest(
    *,
    version_id: str,
    source_name: str,
    source_url: str,
    license_name: str,
    citation: str,
    reviews_path: Path,
    events_path: Path,
    review_row_count: int,
    event_row_count: int,
    review_column_mapping: Mapping[str, str],
    event_column_mapping: Mapping[str, str],
    imported_at: str | None = None,
) -> dict[str, object]:
    """Build the complete, JSON-serialisable provenance contract for one import."""

    return {
        "manifest_version": 1,
        "version_id": version_id,
        "source": {
            "name": source_name,
            "url": source_url,
            "license": license_name,
            "citation": citation,
        },
        "imported_at": imported_at or utc_timestamp(),
        "files": {
            "reviews": {
                "path": str(reviews_path),
                "sha256": file_sha256(reviews_path),
                "row_count": review_row_count,
            },
            "market_events": {
                "path": str(events_path),
                "sha256": file_sha256(events_path),
                "row_count": event_row_count,
            },
        },
        "column_mapping": {
            "reviews": dict(review_column_mapping),
            "market_events": dict(event_column_mapping),
        },
    }


def write_manifest(path: Path, manifest: Mapping[str, object]) -> None:
    """Write a validated import record only after the database transaction succeeds."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
