"""Contract tests for the offline EmbodiedOps evaluation artifact."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from signalforge.embodied.eval import (
    EmbodiedMetrics,
    EvalReport,
    GoldCase,
    Prediction,
    evaluate_embodied_diagnosis,
    run_embodied_eval,
)

ROOT = Path(__file__).resolve().parents[2]


def _gold(**overrides: object) -> GoldCase:
    payload: dict[str, object] = {
        "case_id": "case-1",
        "episode_id": "ep-1",
        "dataset_version_id": "embodied-demo-v1",
        "failure_phase": "grasp",
        "supporting_event_ids": ["ep-1-event-001"],
        "counter_event_ids": ["ep-1-event-002"],
        "decision_status": "actionable",
        "allowed_event_ids": ["ep-1-event-001", "ep-1-event-002"],
        "manual_review_status": "not_performed",
    }
    payload.update(overrides)
    return GoldCase.model_validate(payload)


def _prediction(**overrides: object) -> Prediction:
    payload: dict[str, object] = {
        "episode_id": "ep-1",
        "dataset_version_id": "embodied-demo-v1",
        "failure_phase": "grasp",
        "supporting_event_ids": ["ep-1-event-001"],
        "counter_event_ids": ["ep-1-event-002"],
        "decision_status": "actionable",
        "accepted": True,
        "validation_code": "VALIDATED",
    }
    payload.update(overrides)
    return Prediction.model_validate(payload)


def test_evaluate_keeps_rate_numerators_and_denominators() -> None:
    metrics = evaluate_embodied_diagnosis(_gold(), _prediction())

    assert isinstance(metrics, EmbodiedMetrics)
    assert metrics.phase_accuracy.numerator == 1
    assert metrics.phase_accuracy.denominator == 1
    assert metrics.event_evidence_precision.numerator == 1
    assert metrics.event_evidence_precision.denominator == 1
    assert metrics.event_evidence_coverage.numerator == 1
    assert metrics.event_evidence_coverage.denominator == 1
    assert metrics.refusal_correctness.numerator == 1
    assert metrics.refusal_correctness.denominator == 1


def test_evaluate_does_not_count_unknown_event_as_valid_evidence() -> None:
    metrics = evaluate_embodied_diagnosis(
        _gold(),
        _prediction(supporting_event_ids=["not-allowed"]),
    )

    assert metrics.event_evidence_precision.numerator == 0
    assert metrics.unknown_event_ids == ("not-allowed",)
    assert metrics.validated_event_evidence.numerator == 0


def test_refusal_correctness_is_scored_against_gold_refusal() -> None:
    metrics = evaluate_embodied_diagnosis(
        _gold(decision_status="refused", supporting_event_ids=[], counter_event_ids=[]),
        _prediction(
            decision_status="refused",
            accepted=False,
            validation_code="EVIDENCE_INSUFFICIENT",
            supporting_event_ids=[],
            counter_event_ids=[],
        ),
    )

    assert metrics.refusal_correctness.numerator == 1
    assert metrics.refusal_correctness.denominator == 1


def test_zero_denominator_is_null_not_zero_rate() -> None:
    metrics = evaluate_embodied_diagnosis(
        _gold(supporting_event_ids=[], counter_event_ids=[]),
        _prediction(supporting_event_ids=[], counter_event_ids=[]),
    )

    assert metrics.event_evidence_precision.denominator == 0
    assert metrics.event_evidence_precision.rate is None


def test_eval_report_separates_raw_model_and_validated_system() -> None:
    report = EvalReport(
        raw_model={"attempt_count": 1, "accepted_count": 0},
        validated_system={"accepted_count": 1},
        runtime={"provider": "offline_fixture"},
        limitations=["synthetic cases", "manual review not performed"],
    )
    serialized = json.loads(report.model_dump_json())
    assert set(serialized) == {"raw_model", "validated_system", "runtime", "limitations"}
    assert (
        "accepted_count" not in serialized["raw_model"]
        or serialized["raw_model"]["accepted_count"] == 0
    )


def test_offline_fixture_is_replayable_and_explicitly_synthetic() -> None:
    fixture = ROOT / "eval" / "embodied_gold_cases.jsonl"
    first = ROOT / "eval" / "_test_embodied_results_a.json"
    second = ROOT / "eval" / "_test_embodied_results_b.json"
    try:
        first_report = run_embodied_eval(fixture_path=fixture, output_path=first)
        second_report = run_embodied_eval(fixture_path=fixture, output_path=second)
        assert first_report.model_dump() == second_report.model_dump()
        assert first.read_bytes() == second.read_bytes()
        assert first_report.runtime["provider"] == "offline_fixture"
        assert all(
            row["manual_review_status"] == "not_performed"
            for row in first_report.validated_system["cases"]
        )
    finally:
        first.unlink(missing_ok=True)
        second.unlink(missing_ok=True)


def test_cli_writes_machine_readable_report() -> None:
    fixture = ROOT / "eval" / "embodied_gold_cases.jsonl"
    output = ROOT / "eval" / "_test_embodied_cli.json"
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "eval/run_embodied_eval.py",
                "--fixture",
                str(fixture),
                "--output",
                str(output),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assert "offline_fixture" in completed.stdout
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert set(payload) == {"raw_model", "validated_system", "runtime", "limitations"}
    finally:
        output.unlink(missing_ok=True)


@pytest.mark.parametrize("path", [ROOT / "data" / "embodied" / "demo_diagnosis_snapshot.json"])
def test_demo_snapshot_discloses_offline_validation_and_source(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["snapshot_type"] == "offline_validation_snapshot"
    assert payload["runtime"]["provider"] != "live_qwen"
    assert payload["source"]["episode_ids"]
    assert payload["source"]["dataset_version_id"]
    assert payload["limitations"]["manual_review_status"] == "not_performed"
