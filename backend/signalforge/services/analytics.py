"""DuckDB-backed, reproducible review-topic metrics."""

from __future__ import annotations

from dataclasses import dataclass, field

from signalforge.db.connection import Database
from signalforge.services.scoring import OpportunityInputs

_VALID_SENTIMENTS = frozenset({"positive", "neutral", "negative", "unknown"})


@dataclass(frozen=True, slots=True)
class TopicFilters:
    """Optional, typed filters applied to a dataset-version-scoped query."""

    aspects: tuple[str, ...] | None = None
    sentiment: str | None = None
    minimum_rating: int | None = None
    maximum_rating: int | None = None

    def __post_init__(self) -> None:
        if self.aspects is not None and not self.aspects:
            raise ValueError("aspects cannot be empty when provided")
        if self.sentiment is not None and self.sentiment not in _VALID_SENTIMENTS:
            raise ValueError("sentiment must be positive, neutral, negative, or unknown")
        if self.minimum_rating is not None and not 1 <= self.minimum_rating <= 5:
            raise ValueError("minimum_rating must be between 1 and 5")
        if self.maximum_rating is not None and not 1 <= self.maximum_rating <= 5:
            raise ValueError("maximum_rating must be between 1 and 5")
        if (
            self.minimum_rating is not None
            and self.maximum_rating is not None
            and self.minimum_rating > self.maximum_rating
        ):
            raise ValueError("minimum_rating cannot exceed maximum_rating")


@dataclass(frozen=True, slots=True)
class TopicMetric:
    """One aspect's SQL-derived measurements, never an LLM-derived metric."""

    dataset_version_id: str
    aspect: str
    review_count: int
    negative_count: int
    negative_rate: float | None
    average_rating: float | None
    severity: float | None
    affected: float | None
    evidence: float | None
    scoreable: bool
    missing_fields: list[str] = field(default_factory=list)

    def to_opportunity_inputs(self, *, business_fit: float | None) -> OpportunityInputs:
        """Add the product owner's explicit fit input to this derived metric."""

        return OpportunityInputs(
            affected=self.affected,
            negativity=self.negative_rate,
            severity=self.severity,
            business_fit=business_fit,
            evidence=self.evidence,
        )


def _build_where_clause(
    dataset_version_id: str, filters: TopicFilters
) -> tuple[str, list[object]]:
    clauses = ["dataset_version_id = ?"]
    parameters: list[object] = [dataset_version_id]
    if filters.aspects:
        placeholders = ", ".join("?" for _ in filters.aspects)
        clauses.append(
            f"COALESCE(NULLIF(aspect, ''), 'unknown') IN ({placeholders})"
        )
        parameters.extend(filters.aspects)
    if filters.sentiment is not None:
        clauses.append("sentiment = ?")
        parameters.append(filters.sentiment)
    if filters.minimum_rating is not None:
        clauses.append("rating >= ?")
        parameters.append(filters.minimum_rating)
    if filters.maximum_rating is not None:
        clauses.append("rating <= ?")
        parameters.append(filters.maximum_rating)
    return " AND ".join(clauses), parameters


def list_topic_metrics(
    db: Database, version_id: str, filters: TopicFilters | None = None
) -> list[TopicMetric]:
    """Return aspect metrics calculated entirely by one parameterized DuckDB query.

    A missing rating or unlabelled sentiment makes the relevant derived score
    unavailable.  The returned metric carries that state instead of replacing
    the unknown measurement with a fabricated zero.
    """

    active_filters = filters or TopicFilters()
    where_clause, parameters = _build_where_clause(version_id, active_filters)
    rows = db.execute(
        f"""
        WITH filtered_reviews AS (
            SELECT
                id,
                COALESCE(NULLIF(aspect, ''), 'unknown') AS normalized_aspect,
                rating,
                sentiment
            FROM reviews
            WHERE {where_clause}
        ),
        totals AS (SELECT COUNT(*) AS total_count FROM filtered_reviews)
        SELECT
            normalized_aspect AS aspect,
            COUNT(*) AS review_count,
            COUNT(*) FILTER (WHERE sentiment = 'negative') AS negative_count,
            COUNT(*) FILTER (WHERE sentiment IN ('positive', 'neutral', 'negative'))
                AS labelled_sentiment_count,
            COUNT(rating) AS rating_count,
            AVG(rating) AS average_rating,
            totals.total_count AS total_count
        FROM filtered_reviews
        CROSS JOIN totals
        GROUP BY normalized_aspect, totals.total_count
        ORDER BY negative_count DESC, review_count DESC, aspect ASC
        """,
        tuple(parameters),
    ).fetchall()

    metrics: list[TopicMetric] = []
    for (
        aspect,
        review_count,
        negative_count,
        labelled_sentiment_count,
        rating_count,
        average_rating,
        total_count,
    ) in rows:
        count = int(review_count)
        missing_fields: list[str] = []
        if int(labelled_sentiment_count) != count:
            missing_fields.append("negative_rate")
            negative_rate: float | None = None
        else:
            negative_rate = round(int(negative_count) / count * 100, 1) if count else None
        if int(rating_count) != count or average_rating is None:
            missing_fields.extend(["average_rating", "severity"])
            normalized_average_rating: float | None = None
            severity: float | None = None
        else:
            normalized_average_rating = round(float(average_rating), 1)
            severity = round((5 - float(average_rating)) / 4 * 100, 1)

        affected = round(count / int(total_count) * 100, 1) if total_count else None
        # Ten directly supporting reviews are considered sufficient coverage for
        # the demo rubric; smaller samples retain a proportional evidence score.
        evidence = round(min(count / 10, 1) * 100, 1) if count else None
        if affected is None:
            missing_fields.append("affected")
        if evidence is None:
            missing_fields.append("evidence")
        metrics.append(
            TopicMetric(
                dataset_version_id=version_id,
                aspect=str(aspect),
                review_count=count,
                negative_count=int(negative_count),
                negative_rate=negative_rate,
                average_rating=normalized_average_rating,
                severity=severity,
                affected=affected,
                evidence=evidence,
                scoreable=not missing_fields,
                missing_fields=missing_fields,
            )
        )
    return metrics
