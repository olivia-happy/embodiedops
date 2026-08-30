"""Evidence-bound business metrics for EmbodiedOps.

This module is deliberately a post-hoc evaluation layer.  It receives only
validated outcome labels and approved human annotations; it never controls a
robot, invokes a model, or turns synthetic fixture results into an ROI claim.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import median
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from signalforge.embodied.annotations import RootCauseAnnotation


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BusinessRate(_Strict):
    """An auditable rate that never replaces an empty denominator with zero."""

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def rate_matches_counts(self) -> BusinessRate:
        if self.denominator == 0 and self.rate is not None:
            raise ValueError("zero-denominator rates must be null")
        if self.denominator > 0 and self.numerator > self.denominator:
            raise ValueError("rate numerator cannot exceed denominator")
        expected = self.numerator / self.denominator if self.denominator else None
        if self.rate != expected:
            raise ValueError("rate must equal numerator / denominator")
        return self


class SuccessUplift(_Strict):
    """Before/after comparison with the source denominators retained."""

    before: BusinessRate
    after: BusinessRate
    percentage_point_delta: float | None = None

    @model_validator(mode="after")
    def delta_matches_rates(self) -> SuccessUplift:
        expected = (
            self.after.rate - self.before.rate
            if self.before.rate is not None and self.after.rate is not None
            else None
        )
        if self.percentage_point_delta != expected:
            raise ValueError("percentage_point_delta must match before/after rates")
        return self


class BusinessOutcome(_Strict):
    """A validated experiment outcome, not a raw model response.

    The first seven fields are the transport contract.  Optional evidence
    labels are intentionally tri-state: absent labels remain absent from their
    metric denominators rather than silently becoming failures.
    """

    episode_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    before_success: bool
    after_success: bool
    human_accepted: bool | None = None
    diagnosis_latency_ms: float | None = Field(default=None, ge=0)
    experiment_id: str | None = Field(default=None, min_length=1)
    collision: bool | None = None
    timeout: bool | None = None
    diagnosis_correct: bool | None = None
    unknown_event_referenced: bool | None = None

    @field_validator("episode_id", "dataset_version_id", "experiment_id")
    @classmethod
    def text_fields_cannot_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("text fields cannot be blank")
        return value

    @field_validator("diagnosis_latency_ms")
    @classmethod
    def latency_must_be_finite(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("diagnosis_latency_ms must be finite")
        return value


class BusinessMetrics(_Strict):
    success_rate: BusinessRate
    collision_rate: BusinessRate
    timeout_rate: BusinessRate
    diagnosis_accuracy: BusinessRate
    unknown_event_rate: BusinessRate
    human_acceptance_rate: BusinessRate
    median_diagnosis_latency_ms: float | None = Field(default=None, ge=0)
    success_uplift: SuccessUplift


def _rate(numerator: int, denominator: int) -> BusinessRate:
    return BusinessRate(
        numerator=numerator,
        denominator=denominator,
        rate=numerator / denominator if denominator else None,
    )


def _approved_annotation_keys(
    annotations: Sequence[RootCauseAnnotation | Mapping[str, object]],
) -> set[tuple[str, str]]:
    """Return episode keys that have independently reviewed human labels."""

    approved_statuses = {"double_review", "adjudicated"}
    parsed = [RootCauseAnnotation.model_validate(annotation) for annotation in annotations]
    return {
        (annotation.dataset_version_id, annotation.episode_id)
        for annotation in parsed
        if annotation.review_status in approved_statuses
    }


def _label_rate(
    outcomes: Sequence[BusinessOutcome],
    approved_keys: set[tuple[str, str]],
    field_name: str,
) -> BusinessRate:
    labeled = [
        outcome
        for outcome in outcomes
        if (outcome.dataset_version_id, outcome.episode_id) in approved_keys
        and getattr(outcome, field_name) is not None
    ]
    return _rate(sum(bool(getattr(outcome, field_name)) for outcome in labeled), len(labeled))


def _optional_bool_rate(outcomes: Sequence[BusinessOutcome], field_name: str) -> BusinessRate:
    labeled = [outcome for outcome in outcomes if getattr(outcome, field_name) is not None]
    return _rate(sum(bool(getattr(outcome, field_name)) for outcome in labeled), len(labeled))


def aggregate_business_metrics(
    outcomes: Sequence[BusinessOutcome | Mapping[str, object]],
    annotations: Sequence[RootCauseAnnotation | Mapping[str, object]],
) -> BusinessMetrics:
    """Aggregate business metrics from validated outcomes and reviewed labels.

    Diagnosis accuracy and unknown-event rate are only evaluated where an
    episode has a double-reviewed or adjudicated annotation.  This prevents a
    model output or a single annotator from becoming business "ground truth".
    """

    parsed_outcomes = [BusinessOutcome.model_validate(outcome) for outcome in outcomes]
    approved_keys = _approved_annotation_keys(annotations)
    outcome_count = len(parsed_outcomes)
    before = _rate(sum(outcome.before_success for outcome in parsed_outcomes), outcome_count)
    after = _rate(sum(outcome.after_success for outcome in parsed_outcomes), outcome_count)
    latencies = [
        outcome.diagnosis_latency_ms
        for outcome in parsed_outcomes
        if outcome.diagnosis_latency_ms is not None
    ]
    return BusinessMetrics(
        success_rate=after,
        collision_rate=_optional_bool_rate(parsed_outcomes, "collision"),
        timeout_rate=_optional_bool_rate(parsed_outcomes, "timeout"),
        diagnosis_accuracy=_label_rate(parsed_outcomes, approved_keys, "diagnosis_correct"),
        unknown_event_rate=_label_rate(
            parsed_outcomes,
            approved_keys,
            "unknown_event_referenced",
        ),
        human_acceptance_rate=_optional_bool_rate(parsed_outcomes, "human_accepted"),
        median_diagnosis_latency_ms=float(median(latencies)) if latencies else None,
        success_uplift=SuccessUplift(
            before=before,
            after=after,
            percentage_point_delta=after.rate - before.rate
            if after.rate is not None and before.rate is not None
            else None,
        ),
    )


def _cohort_metrics(
    value: BusinessMetrics | Mapping[str, object],
) -> tuple[BusinessMetrics, Mapping[str, object]]:
    if isinstance(value, BusinessMetrics):
        return value, {}
    data = dict(value)
    if "metrics" in data:
        metric_payload = data.pop("metrics")
    else:
        metric_fields = set(BusinessMetrics.model_fields)
        metric_payload = {
            field_name: data.pop(field_name)
            for field_name in tuple(data)
            if field_name in metric_fields
        }
    metrics = BusinessMetrics.model_validate(metric_payload)
    return metrics, data


def _has_holdout(cohort: Mapping[str, object]) -> bool:
    if "has_holdout" in cohort:
        return cohort["has_holdout"] is True
    policy = cohort.get("holdout_policy")
    return isinstance(policy, str) and policy.strip().lower() not in {"", "none", "unknown"}


def _has_permissioned_provenance(cohort: Mapping[str, object]) -> bool:
    if "permissioned_provenance" in cohort:
        return cohort["permissioned_provenance"] is True
    permission = cohort.get("license_or_permission")
    return isinstance(permission, str) and permission.strip().lower() not in {
        "",
        "none",
        "unknown",
        "not_applicable",
    }


def _metric_delta(sim: BusinessRate, real: BusinessRate) -> float | None:
    if sim.rate is None or real.rate is None:
        return None
    return real.rate - sim.rate


def evaluate_sim_to_real(
    sim_metrics: BusinessMetrics | Mapping[str, object],
    real_metrics: BusinessMetrics | Mapping[str, object],
) -> dict[str, object]:
    """Compare cohorts without claiming transfer unless both gates are met.

    A numerical delta is descriptive only.  ``eligible_for_review`` means
    that data provenance is adequate for human review, not that sim-to-real
    transfer has been proven.
    """

    sim, sim_cohort = _cohort_metrics(sim_metrics)
    real, real_cohort = _cohort_metrics(real_metrics)
    refusal_reasons = []
    for prefix, cohort in (("sim", sim_cohort), ("real", real_cohort)):
        if not _has_holdout(cohort):
            refusal_reasons.append(f"{prefix}_missing_holdout")
        if not _has_permissioned_provenance(cohort):
            refusal_reasons.append(f"{prefix}_missing_permissioned_provenance")
    if real_cohort and real_cohort.get("real_robot_data") is not True:
        refusal_reasons.append("real_not_marked_real_robot_data")

    metric_deltas = {
        "success_rate": _metric_delta(sim.success_rate, real.success_rate),
        "collision_rate": _metric_delta(sim.collision_rate, real.collision_rate),
        "timeout_rate": _metric_delta(sim.timeout_rate, real.timeout_rate),
        "diagnosis_accuracy": _metric_delta(sim.diagnosis_accuracy, real.diagnosis_accuracy),
        "unknown_event_rate": _metric_delta(sim.unknown_event_rate, real.unknown_event_rate),
        "human_acceptance_rate": _metric_delta(
            sim.human_acceptance_rate,
            real.human_acceptance_rate,
        ),
        "success_uplift": (
            real.success_uplift.percentage_point_delta - sim.success_uplift.percentage_point_delta
            if real.success_uplift.percentage_point_delta is not None
            and sim.success_uplift.percentage_point_delta is not None
            else None
        ),
    }
    return {
        "transfer_claim_status": "refused" if refusal_reasons else "eligible_for_review",
        "refusal_reasons": refusal_reasons,
        "metric_deltas": metric_deltas,
        "limitations": [
            "Metric deltas do not establish causal or production ROI impact.",
            "Human review is required before any real-robot rollout decision.",
        ],
    }


def run_embodied_business_eval(*, fixture_path: Path, output_path: Path) -> dict[str, object]:
    """Run a replayable, synthetic business fixture without a model call."""

    rows: list[Mapping[str, Any]] = []
    for line in fixture_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if not isinstance(row, Mapping):
                raise ValueError("each business evaluation row must be an object")
            rows.append(row)
    if not rows:
        raise ValueError("business evaluation fixture must contain at least one case")
    if any(row.get("real_robot_data") is not False for row in rows):
        raise ValueError("offline business fixture must set real_robot_data=false")
    if any(row.get("manual_review_status") != "not_performed" for row in rows):
        raise ValueError("offline business fixture must set manual_review_status=not_performed")

    outcomes = [BusinessOutcome.model_validate(row["outcome"]) for row in rows]
    annotations = [
        RootCauseAnnotation.model_validate(annotation)
        for row in rows
        for annotation in row.get("annotations", [])
    ]
    report: dict[str, object] = {
        "raw_model": {
            "provider": "offline_fixture",
            "attempt_count": 0,
            "note": "No raw model response is used in business metric aggregation.",
        },
        "validated_system": {
            "approved_annotation_count": len(_approved_annotation_keys(annotations)),
            "fixture_case_count": len(rows),
        },
        "business_outcomes": aggregate_business_metrics(
            outcomes,
            annotations,
        ).model_dump(mode="json"),
        "runtime": {
            "fixture": fixture_path.name,
            "live_model": False,
            "real_robot_data": False,
            "manual_review_status": "not_performed",
        },
        "limitations": [
            "Synthetic fixture; not a production ROI claim.",
            "No permissioned real-robot holdout is included, so sim-to-real transfer is refused.",
            "manual_review_status=not_performed for every fixed offline case.",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


__all__ = [
    "BusinessMetrics",
    "BusinessOutcome",
    "BusinessRate",
    "SuccessUplift",
    "aggregate_business_metrics",
    "evaluate_sim_to_real",
    "run_embodied_business_eval",
]
