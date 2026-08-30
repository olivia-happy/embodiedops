"""Deterministic, offline evaluation contracts for EmbodiedOps diagnosis.

This module intentionally evaluates a supplied prediction mapping; it never
starts a model, contacts Ollama, or treats an unvalidated citation as valid.
The output keeps raw-provider attempts separate from the post-validation
system result so a rejected hallucination cannot inflate the model score.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvalRate(_Strict):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def rate_matches_counts(self) -> EvalRate:
        if self.denominator == 0 and self.rate is not None:
            raise ValueError("zero-denominator rates must be null")
        if self.denominator and self.numerator > self.denominator:
            raise ValueError("rate numerator cannot exceed denominator")
        return self


class GoldCase(_Strict):
    case_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    failure_phase: str = Field(min_length=1)
    supporting_event_ids: tuple[str, ...] = ()
    counter_event_ids: tuple[str, ...] = ()
    decision_status: Literal["actionable", "needs_evidence", "refused"]
    allowed_event_ids: tuple[str, ...] = ()
    manual_review_status: Literal["not_performed", "performed"] = "not_performed"


class Prediction(_Strict):
    episode_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    failure_phase: str = Field(min_length=1)
    supporting_event_ids: tuple[str, ...] = ()
    counter_event_ids: tuple[str, ...] = ()
    decision_status: Literal["actionable", "needs_evidence", "refused"]
    accepted: bool = False
    validation_code: str = Field(min_length=1)


class EmbodiedMetrics(_Strict):
    phase_accuracy: EvalRate
    event_evidence_precision: EvalRate
    event_evidence_coverage: EvalRate
    refusal_correctness: EvalRate
    decision_status_accuracy: EvalRate
    validated_event_evidence: EvalRate
    unknown_event_ids: tuple[str, ...] = ()


class EvalReport(_Strict):
    raw_model: dict[str, object]
    validated_system: dict[str, object]
    runtime: dict[str, object]
    limitations: list[str]


def _rate(numerator: int, denominator: int) -> EvalRate:
    return EvalRate(
        numerator=numerator,
        denominator=denominator,
        rate=(numerator / denominator if denominator else None),
    )


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value) for value in values))


def evaluate_embodied_diagnosis(
    gold: GoldCase | Mapping[str, object],
    prediction: Prediction | Mapping[str, object],
) -> EmbodiedMetrics:
    """Score one bounded diagnosis while retaining every metric denominator."""

    expected = GoldCase.model_validate(gold)
    actual = Prediction.model_validate(prediction)
    allowed = set(expected.allowed_event_ids)
    # A gold fixture may omit an explicit allow-list when support/counter IDs
    # are the only permitted references.
    if not allowed:
        allowed = set(expected.supporting_event_ids) | set(expected.counter_event_ids)
    cited_support = list(actual.supporting_event_ids)
    cited_all = [*actual.supporting_event_ids, *actual.counter_event_ids]
    unknown = _ordered_unique([event_id for event_id in cited_all if event_id not in allowed])
    expected_support = set(expected.supporting_event_ids)
    valid_support = [event_id for event_id in cited_support if event_id in allowed]
    correct_support = sum(event_id in expected_support for event_id in valid_support)
    coverage_numerator = len(expected_support & set(valid_support))
    # Precision's denominator is every provider citation, including unknowns.
    phase = _rate(int(actual.failure_phase == expected.failure_phase), 1)
    precision = _rate(correct_support, len(cited_support))
    coverage = _rate(coverage_numerator, len(expected_support))
    expected_refusal = expected.decision_status == "refused"
    predicted_refusal = (actual.decision_status == "refused") or not actual.accepted
    refusal = _rate(int(predicted_refusal == expected_refusal), 1)
    status = _rate(int(actual.decision_status == expected.decision_status), 1)
    validated = _rate(correct_support, len(valid_support))
    return EmbodiedMetrics(
        phase_accuracy=phase,
        event_evidence_precision=precision,
        event_evidence_coverage=coverage,
        refusal_correctness=refusal,
        decision_status_accuracy=status,
        validated_event_evidence=validated,
        unknown_event_ids=unknown,
    )


def _load_rows(path: Path) -> list[tuple[GoldCase, Prediction]]:
    rows: list[tuple[GoldCase, Prediction]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, Mapping):
            raise ValueError("each evaluation row must be an object")
        rows.append(
            (
                GoldCase.model_validate(row["gold"]),
                Prediction.model_validate(row["prediction"]),
            )
        )
    if not rows:
        raise ValueError("evaluation fixture must contain at least one case")
    return rows


def _aggregate(rows: Sequence[tuple[GoldCase, Prediction, EmbodiedMetrics]]) -> dict[str, object]:
    names = (
        "phase_accuracy",
        "event_evidence_precision",
        "event_evidence_coverage",
        "refusal_correctness",
        "decision_status_accuracy",
        "validated_event_evidence",
    )
    metrics: dict[str, object] = {}
    for name in names:
        numerator = sum(getattr(item[2], name).numerator for item in rows)
        denominator = sum(getattr(item[2], name).denominator for item in rows)
        metrics[name] = _rate(numerator, denominator).model_dump(mode="json")
    return metrics


def run_embodied_eval(*, fixture_path: Path, output_path: Path) -> EvalReport:
    """Run the replayable gold/prediction fixture without a model call."""

    pairs = _load_rows(fixture_path)
    scored = [
        (gold, prediction, evaluate_embodied_diagnosis(gold, prediction))
        for gold, prediction in pairs
    ]
    cases = []
    for gold, _prediction, metrics in scored:
        metric_dump = metrics.model_dump(mode="json")
        # Unknown references remain an audit signal, but are deliberately not
        # persisted in the validated-system diagnosis payload.
        metric_dump.pop("unknown_event_ids", None)
        cases.append(
            {
            "case_id": gold.case_id,
            "episode_id": gold.episode_id,
            "dataset_version_id": gold.dataset_version_id,
            "manual_review_status": gold.manual_review_status,
                "metrics": metric_dump,
            }
        )
    report = EvalReport(
        raw_model={
            "provider": "offline_fixture",
            "attempt_count": 0,
            "accepted_count": 0,
            "rejected_unknown_event_count": sum(
                len(metrics.unknown_event_ids) for _gold, _prediction, metrics in scored
            ),
            "rejected_unknown_event_ids": [
                event_id
                for _gold, _prediction, metrics in scored
                for event_id in metrics.unknown_event_ids
            ],
            "note": (
                "No live provider output was scored; fixture predictions are "
                "post-validation inputs."
            ),
        },
        validated_system={
            "case_count": len(scored),
            "metrics": _aggregate(scored),
            "persisted_unknown_event_ids": 0,
            "cases": cases,
        },
        runtime={
            "provider": "offline_fixture",
            "mode": "offline_validation",
            "fixture": fixture_path.name,
        },
        limitations=[
            "Synthetic fixture; not a production or real-robot quality claim.",
            "manual_review_status=not_performed for synthetic cases.",
            "Raw model quality is not measured because no live provider was called.",
        ],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return report


__all__ = [
    "EmbodiedMetrics",
    "EvalRate",
    "EvalReport",
    "GoldCase",
    "Prediction",
    "evaluate_embodied_diagnosis",
    "run_embodied_eval",
]
