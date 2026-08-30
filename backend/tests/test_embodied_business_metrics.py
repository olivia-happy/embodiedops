"""Tests for the evidence-bound EmbodiedOps business metric layer."""

from __future__ import annotations

from pathlib import Path

from signalforge.embodied.annotations import RootCauseAnnotation
from signalforge.embodied.business_metrics import (
    BusinessOutcome,
    aggregate_business_metrics,
    evaluate_sim_to_real,
    run_embodied_business_eval,
)

ROOT = Path(__file__).resolve().parents[2]


def _annotation(**overrides: object) -> RootCauseAnnotation:
    payload: dict[str, object] = {
        "annotation_id": "annotation-1",
        "dataset_version_id": "dataset-v1",
        "episode_id": "episode-1",
        "failure_phase": "grasp",
        "root_cause": "contact was not retained",
        "supporting_event_ids": ["event-1"],
        "counter_event_ids": [],
        "confidence": 0.9,
        "annotator_id": "reviewer-a",
        "review_status": "double_review",
        "reviewer_id": "reviewer-b",
    }
    payload.update(overrides)
    return RootCauseAnnotation.model_validate(payload)


def _outcome(**overrides: object) -> BusinessOutcome:
    payload: dict[str, object] = {
        "episode_id": "episode-1",
        "dataset_version_id": "dataset-v1",
        "before_success": False,
        "after_success": True,
        "human_accepted": True,
        "diagnosis_latency_ms": 120.0,
        "experiment_id": "experiment-1",
        "collision": False,
        "timeout": False,
        "diagnosis_correct": True,
        "unknown_event_referenced": False,
    }
    payload.update(overrides)
    return BusinessOutcome.model_validate(payload)


def test_aggregate_keeps_explicit_numerators_denominators_and_uplift() -> None:
    outcomes = [
        _outcome(),
        _outcome(
            episode_id="episode-2",
            before_success=True,
            after_success=True,
            human_accepted=False,
            diagnosis_latency_ms=180.0,
            collision=True,
            timeout=True,
            diagnosis_correct=False,
            unknown_event_referenced=True,
        ),
    ]
    annotations = [_annotation(), _annotation(annotation_id="annotation-2", episode_id="episode-2")]

    metrics = aggregate_business_metrics(outcomes, annotations)

    assert metrics.success_rate.numerator == 2
    assert metrics.success_rate.denominator == 2
    assert metrics.collision_rate.numerator == 1
    assert metrics.collision_rate.denominator == 2
    assert metrics.timeout_rate.numerator == 1
    assert metrics.diagnosis_accuracy.numerator == 1
    assert metrics.diagnosis_accuracy.denominator == 2
    assert metrics.unknown_event_rate.numerator == 1
    assert metrics.human_acceptance_rate.numerator == 1
    assert metrics.human_acceptance_rate.denominator == 2
    assert metrics.median_diagnosis_latency_ms == 150.0
    assert metrics.success_uplift.before.numerator == 1
    assert metrics.success_uplift.before.denominator == 2
    assert metrics.success_uplift.after.numerator == 2
    assert metrics.success_uplift.after.denominator == 2
    assert metrics.success_uplift.percentage_point_delta == 0.5


def test_empty_or_unlabelled_inputs_emit_null_rates_not_zero_percent() -> None:
    empty = aggregate_business_metrics([], [])
    assert empty.success_rate.numerator == 0
    assert empty.success_rate.denominator == 0
    assert empty.success_rate.rate is None
    assert empty.median_diagnosis_latency_ms is None
    assert empty.success_uplift.percentage_point_delta is None

    unlabeled = aggregate_business_metrics(
        [_outcome(diagnosis_correct=None, unknown_event_referenced=None)],
        [],
    )
    assert unlabeled.diagnosis_accuracy.denominator == 0
    assert unlabeled.diagnosis_accuracy.rate is None
    assert unlabeled.unknown_event_rate.denominator == 0
    assert unlabeled.unknown_event_rate.rate is None


def test_accuracy_requires_an_approved_human_annotation_for_the_same_episode() -> None:
    metrics = aggregate_business_metrics([_outcome()], [_annotation(review_status="single_review")])

    assert metrics.diagnosis_accuracy.denominator == 0
    assert metrics.unknown_event_rate.denominator == 0


def test_sim_to_real_refuses_without_holdout_or_permissioned_provenance() -> None:
    metrics = aggregate_business_metrics([_outcome()], [_annotation()])

    result = evaluate_sim_to_real(
        {"metrics": metrics, "has_holdout": True, "permissioned_provenance": True},
        {
            "metrics": metrics,
            "has_holdout": False,
            "permissioned_provenance": False,
            "real_robot_data": True,
        },
    )

    assert result["transfer_claim_status"] == "refused"
    assert "real_missing_holdout" in result["refusal_reasons"]
    assert "real_missing_permissioned_provenance" in result["refusal_reasons"]
    assert result["metric_deltas"]["success_rate"] == 0.0


def test_sim_to_real_reports_deltas_only_for_permissioned_holdouts() -> None:
    sim_metrics = aggregate_business_metrics([_outcome(after_success=False)], [_annotation()])
    real_metrics = aggregate_business_metrics([_outcome(after_success=True)], [_annotation()])

    result = evaluate_sim_to_real(
        {"metrics": sim_metrics, "has_holdout": True, "permissioned_provenance": True},
        {
            "metrics": real_metrics,
            "has_holdout": True,
            "permissioned_provenance": True,
            "real_robot_data": True,
        },
    )

    assert result["transfer_claim_status"] == "eligible_for_review"
    assert result["refusal_reasons"] == []
    assert result["metric_deltas"]["success_rate"] == 1.0


def test_fixed_business_fixture_is_explicitly_synthetic_and_replayable() -> None:
    fixture = ROOT / "eval" / "embodied_business_cases.jsonl"
    output = ROOT / "eval" / "_test_embodied_business_results.json"
    try:
        report = run_embodied_business_eval(fixture_path=fixture, output_path=output)

        assert report["runtime"]["real_robot_data"] is False
        assert report["runtime"]["manual_review_status"] == "not_performed"
        assert report["business_outcomes"]["success_rate"]["denominator"] == 3
        assert "not a production ROI claim" in report["limitations"][0]
    finally:
        output.unlink(missing_ok=True)
