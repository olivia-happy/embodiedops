"""Validation-first, idempotent loading of review and market-event snapshots."""

from __future__ import annotations

import argparse
import csv
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from signalforge.core.models import DatasetVersion
from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version
from signalforge.etl.manifest import build_manifest, file_sha256, write_manifest
from signalforge.etl.normalize import NormalizationError, NormalizedReview, normalize_review

REVIEW_REQUIRED = ("review_id", "review", "rating")
EVENT_REQUIRED = (
    "source_url",
    "published_on",
    "excerpt",
    "event_type",
    "industry",
    "evidence_quality",
)
DEFAULT_REVIEW_MAPPING = {
    "review_id": "review_id",
    "review": "review",
    "rating": "rating",
    "aspect": "aspect",
    "sentiment": "sentiment",
}
DEFAULT_EVENT_MAPPING = {
    "event_id": "event_id",
    "source_url": "source_url",
    "published_on": "published_on",
    "excerpt": "excerpt",
    "event_type": "event_type",
    "industry": "industry",
    "evidence_quality": "evidence_quality",
}


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """The declared provenance and field mapping for a reproducible snapshot."""

    slug: str
    source_name: str
    source_url: str
    license_name: str
    citation: str
    manifest_dir: Path = Path("../data/manifests")
    review_column_mapping: Mapping[str, str] = field(default_factory=lambda: DEFAULT_REVIEW_MAPPING)
    event_column_mapping: Mapping[str, str] = field(default_factory=lambda: DEFAULT_EVENT_MAPPING)


@dataclass(frozen=True, slots=True)
class RowError:
    """A user-actionable failure associated with an input row or header."""

    row_number: int | None
    field: str
    message: str


class ImportValidationError(ValueError):
    """Raised before a transaction if an import cannot produce a complete snapshot."""

    def __init__(self, errors: Sequence[RowError]) -> None:
        self.errors = tuple(errors)
        summary = "; ".join(
            f"row {error.row_number or 'header'} {error.field}: {error.message}"
            for error in self.errors
        )
        super().__init__(summary)


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """A validated public event record suitable for the local risk radar."""

    id: str
    source_url: str
    published_on: date
    excerpt: str
    event_type: str
    industry: str
    evidence_quality: int


def _utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None:
                raise ImportValidationError([RowError(None, "file", "CSV header is required")])
            return list(reader.fieldnames), list(reader)
    except UnicodeDecodeError as error:
        raise ImportValidationError(
            [RowError(None, "file", "CSV must be UTF-8 encoded")]
        ) from error


def _validate_mapping(
    headers: Sequence[str], mapping: Mapping[str, str], required: Sequence[str]
) -> list[RowError]:
    errors: list[RowError] = []
    for field_name in required:
        source_column = mapping.get(field_name)
        if not source_column:
            errors.append(RowError(None, field_name, "required field has no column mapping"))
        elif source_column not in headers:
            errors.append(RowError(None, field_name, f"mapped column '{source_column}' is missing"))
    return errors


def _map_row(raw: Mapping[str, str], mapping: Mapping[str, str]) -> dict[str, str]:
    return {
        field_name: raw[source_column]
        for field_name, source_column in mapping.items()
        if source_column in raw
    }


def _normalize_reviews(
    rows: Sequence[Mapping[str, str]], metadata: SourceMetadata
) -> tuple[list[NormalizedReview], list[RowError]]:
    reviews: list[NormalizedReview] = []
    errors: list[RowError] = []
    seen_ids: set[str] = set()
    for row_number, raw in enumerate(rows, start=2):
        try:
            review = normalize_review(
                _map_row(raw, metadata.review_column_mapping), source_slug=metadata.slug
            )
            if review.id in seen_ids:
                raise NormalizationError("duplicate review_id after ID normalization")
            seen_ids.add(review.id)
            reviews.append(review)
        except NormalizationError as error:
            field_name = str(error).split(" ", maxsplit=1)[0]
            errors.append(RowError(row_number, field_name, str(error)))
    return reviews, errors


def _normalize_event(
    raw: Mapping[str, str], *, event_id: str
) -> NormalizedEvent:
    required_values: dict[str, str] = {}
    for field_name in EVENT_REQUIRED:
        value = raw.get(field_name, "").strip()
        if not value:
            raise ValueError(f"{field_name} is required")
        required_values[field_name] = value
    try:
        published_on = date.fromisoformat(required_values["published_on"])
    except ValueError as error:
        raise ValueError("published_on must use YYYY-MM-DD") from error
    try:
        evidence_quality = int(required_values["evidence_quality"])
    except ValueError as error:
        raise ValueError("evidence_quality must be an integer from 0 to 100") from error
    if not 0 <= evidence_quality <= 100:
        raise ValueError("evidence_quality must be an integer from 0 to 100")
    if not required_values["source_url"].startswith(("https://", "http://")):
        raise ValueError("source_url must be an HTTP(S) URL")
    return NormalizedEvent(
        id=event_id,
        source_url=required_values["source_url"],
        published_on=published_on,
        excerpt=required_values["excerpt"],
        event_type=required_values["event_type"],
        industry=required_values["industry"],
        evidence_quality=evidence_quality,
    )


def _normalize_events(
    rows: Sequence[Mapping[str, str]], metadata: SourceMetadata
) -> tuple[list[NormalizedEvent], list[RowError]]:
    events: list[NormalizedEvent] = []
    errors: list[RowError] = []
    seen_ids: set[str] = set()
    for row_number, raw in enumerate(rows, start=2):
        mapped = _map_row(raw, metadata.event_column_mapping)
        raw_event_id = mapped.get("event_id") or f"event-{row_number - 1}"
        event_id = f"{metadata.slug}-{raw_event_id}".replace("/", "-").replace(" ", "-")
        try:
            event = _normalize_event(mapped, event_id=event_id)
            if event.id in seen_ids:
                raise ValueError("event_id is duplicated")
            seen_ids.add(event.id)
            events.append(event)
        except ValueError as error:
            field_name = str(error).split(" ", maxsplit=1)[0]
            errors.append(RowError(row_number, field_name, str(error)))
    return events, errors


def _read_version(db: Database, version_id: str) -> DatasetVersion:
    row = db.execute(
        "SELECT id, source_name, source_url, file_hash, row_count, imported_at "
        "FROM dataset_versions WHERE id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"dataset version {version_id} was not found")
    return DatasetVersion(
        id=row[0],
        source_name=row[1],
        source_url=row[2],
        file_hash=row[3],
        row_count=row[4],
        imported_at=row[5],
    )


def _manifest_review_mapping(mapping: Mapping[str, str]) -> dict[str, str]:
    """Expose output-field names, rather than importer implementation names, in provenance."""

    result = dict(mapping)
    if "review" in result:
        result["content"] = result.pop("review")
    if "review_id" in result:
        result["id"] = result.pop("review_id")
    return result


def load_snapshot(
    db: Database,
    reviews_path: Path,
    events_path: Path,
    source_metadata: SourceMetadata,
) -> DatasetVersion:
    """Validate and atomically load an immutable review/event snapshot.

    Both files are completely read and checked before DuckDB is mutated. A
    repeated import of identical review content returns the existing immutable
    dataset version without inserting duplicated events or reviews.
    """

    reviews_path = Path(reviews_path)
    events_path = Path(events_path)
    review_headers, review_rows = _csv_rows(reviews_path)
    event_headers, event_rows = _csv_rows(events_path)
    errors = _validate_mapping(
        review_headers, source_metadata.review_column_mapping, REVIEW_REQUIRED
    )
    errors.extend(
        _validate_mapping(event_headers, source_metadata.event_column_mapping, EVENT_REQUIRED)
    )
    if errors:
        raise ImportValidationError(errors)
    reviews, review_errors = _normalize_reviews(review_rows, source_metadata)
    events, event_errors = _normalize_events(event_rows, source_metadata)
    errors.extend(review_errors)
    errors.extend(event_errors)
    if errors:
        raise ImportValidationError(errors)

    reviews_hash = file_sha256(reviews_path)
    version_id = f"{source_metadata.slug}-{reviews_hash.removeprefix('sha256:')[:12]}"
    if db.execute("SELECT 1 FROM dataset_versions WHERE id = ?", (version_id,)).fetchone():
        return _read_version(db, version_id)

    imported_at = _utc_now()
    try:
        db.execute("BEGIN TRANSACTION")
        version = insert_dataset_version(
            db,
            version_id,
            source_metadata.source_name,
            reviews_hash,
            len(reviews),
            source_url=source_metadata.source_url,
            imported_at=imported_at,
        )
        for review in reviews:
            db.execute(
                """
                INSERT INTO reviews (
                    id, dataset_version_id, content, rating, aspect, sentiment, redacted
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review.id,
                    version.id,
                    review.content,
                    review.rating,
                    review.aspect,
                    review.sentiment,
                    review.redacted,
                ),
            )
        for event in events:
            db.execute(
                """
                INSERT INTO market_events (
                    id, dataset_version_id, source_url, published_on, excerpt,
                    event_type, industry, evidence_quality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    version.id,
                    event.source_url,
                    event.published_on,
                    event.excerpt,
                    event.event_type,
                    event.industry,
                    event.evidence_quality,
                ),
            )
        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise

    manifest = build_manifest(
        version_id=version.id,
        source_name=source_metadata.source_name,
        source_url=source_metadata.source_url,
        license_name=source_metadata.license_name,
        citation=source_metadata.citation,
        reviews_path=reviews_path,
        events_path=events_path,
        review_row_count=len(reviews),
        event_row_count=len(events),
        review_column_mapping=_manifest_review_mapping(source_metadata.review_column_mapping),
        event_column_mapping=source_metadata.event_column_mapping,
        imported_at=imported_at.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z"),
    )
    write_manifest(source_metadata.manifest_dir / f"{version.id}.json", manifest)
    return version


def demo_source_metadata(*, manifest_dir: Path) -> SourceMetadata:
    """Describe the bundled original, synthetic review demo data accurately."""

    return SourceMetadata(
        slug="asap-demo",
        source_name="SignalForge original synthetic Chinese review sample",
        source_url="https://github.com/Meituan-Dianping/asap",
        license_name="CC0-1.0 (this bundled synthetic sample only)",
        citation=(
            "The bundled rows are original synthetic examples; ASAP is cited only as "
            "the optional external schema reference and is not redistributed here."
        ),
        manifest_dir=manifest_dir,
    )


def main() -> int:
    """Load an explicit local snapshot; intended for the documented demo command."""

    parser = argparse.ArgumentParser(description="Import one SignalForge CSV snapshot")
    parser.add_argument("--reviews", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--database", type=Path, default=Path("../data/signalforge.duckdb"))
    parser.add_argument("--manifest-dir", type=Path, default=Path("../data/manifests"))
    arguments = parser.parse_args()
    database = Database(arguments.database)
    database.apply_schema()
    try:
        version = load_snapshot(
            database,
            arguments.reviews,
            arguments.events,
            demo_source_metadata(manifest_dir=arguments.manifest_dir),
        )
    except ImportValidationError as error:
        for row_error in error.errors:
            location = row_error.row_number if row_error.row_number is not None else "header"
            print(f"Import rejected: row {location}, {row_error.field}: {row_error.message}")
        return 2
    finally:
        database.close()
    print(f"Imported dataset version: {version.id} ({version.row_count} reviews)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
