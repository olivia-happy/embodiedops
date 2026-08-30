"""Tests for DuckDB-derived topic metrics."""

from pathlib import Path

from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version
from signalforge.services.analytics import TopicFilters, list_topic_metrics


def _database_with_reviews(tmp_path: Path) -> tuple[Database, str]:
    db = Database(tmp_path / "analytics.duckdb")
    db.apply_schema()
    version_id = "v1"
    insert_dataset_version(db, version_id, "Fixture", "sha256:fixture", 5)
    db.execute(
        """
        INSERT INTO reviews VALUES
            ('r1', 'v1', 'Service wait was too long', 1, 'service', 'negative', false),
            ('r2', 'v1', 'Support replied slowly', 2, 'service', 'negative', false),
            ('r3', 'v1', 'Booking was clear', 5, 'service', 'positive', false),
            ('r4', 'v1', 'The room was quiet', 5, 'environment', 'positive', false),
            ('r5', 'v1', 'The table was not cleaned', NULL, 'environment', 'negative', false)
        """
    )
    return db, version_id


def test_topic_metrics_are_calculated_in_duckdb(tmp_path: Path) -> None:
    db, version_id = _database_with_reviews(tmp_path)

    metrics = list_topic_metrics(db, version_id)

    service = next(metric for metric in metrics if metric.aspect == "service")
    assert service.review_count == 3
    assert service.negative_count == 2
    assert service.negative_rate == 66.7
    assert service.average_rating == 2.7
    assert service.severity == 58.3
    assert service.scoreable is True


def test_filters_are_bound_parameters_and_change_metric_scope(tmp_path: Path) -> None:
    db, version_id = _database_with_reviews(tmp_path)

    metrics = list_topic_metrics(db, version_id, TopicFilters(sentiment="negative"))

    service = next(metric for metric in metrics if metric.aspect == "service")
    assert service.review_count == 2
    assert service.negative_count == 2
    assert service.negative_rate == 100.0


def test_missing_rating_yields_unscoreable_metric_not_a_zero(tmp_path: Path) -> None:
    db, version_id = _database_with_reviews(tmp_path)

    metrics = list_topic_metrics(db, version_id, TopicFilters(aspects=("environment",)))

    metric = metrics[0]
    assert metric.review_count == 2
    assert metric.average_rating is None
    assert metric.severity is None
    assert metric.scoreable is False
    assert metric.missing_fields == ["average_rating", "severity"]


def test_null_and_blank_aspects_share_one_unknown_metric(tmp_path: Path) -> None:
    db = Database(tmp_path / "unknown-aspect.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "Fixture", "sha256:unknown", 3)
    db.execute(
        """
        INSERT INTO reviews VALUES
            ('r1', 'v1', 'Unlabelled negative review', 1, NULL, 'negative', false),
            ('r2', 'v1', 'Blank-aspect negative review', 2, '', 'negative', false),
            ('r3', 'v1', 'Unlabelled positive review', 5, NULL, 'positive', false)
        """
    )

    metrics = list_topic_metrics(db, "v1")

    assert len(metrics) == 1
    assert metrics[0].aspect == "unknown"
    assert metrics[0].review_count == 3
    assert metrics[0].negative_count == 2
    assert metrics[0].negative_rate == 66.7
