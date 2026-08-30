"""Tests for strict decision-memo domain contracts."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from signalforge.core.models import (
    DecisionMemo,
    EvidencePlan,
    ExperimentPlan,
    MemoFacts,
    MemoGenerationJob,
    TraceRecord,
)


def _facts() -> MemoFacts:
    return MemoFacts(review_count=4, negative_count=3, negative_rate=75.0)


def _memo_fields() -> dict[str, object]:
    return {
        "id": "memo-1",
        "dataset_version_id": "v1",
        "decision_statement": "暂不采取业务动作",
        "topic": "service",
        "facts": _facts(),
        "supporting_evidence": [],
        "counter_evidence": [],
        "counter_evidence_checked": True,
        "unknowns": ["缺少重复子问题证据"],
        "reasoning_summary": "现有问题机制分散。",
        "prompt_version": "decision-memo-v1",
    }


def _evidence_plan() -> EvidencePlan:
    return EvidencePlan(
        candidate_subproblems=["排队透明度", "客服响应时效"],
        collection_fields=["午高峰等待时长", "客服首次响应时长"],
        minimum_evidence_per_subproblem=3,
        reassessment_condition="每个候选子问题积累至少 3 条独立证据后复查。",
    )


def _experiment() -> ExperimentPlan:
    return ExperimentPlan(
        hypothesis="展示取餐进度可以减少等待焦虑。",
        target_segment="午高峰到店顾客",
        intervention="在订单页展示排队位置与预计取餐时间。",
        primary_metric="订单进度查询率",
        guardrail_metric="订单取消率",
        duration_days=14,
        stop_conditions=["订单取消率较基线升高 2 个百分点"],
    )


def test_needs_evidence_memo_requires_evidence_plan() -> None:
    with pytest.raises(ValidationError, match="evidence_plan"):
        DecisionMemo(decision_status="needs_evidence", **_memo_fields())


def test_actionable_memo_requires_experiment_and_subproblem() -> None:
    with pytest.raises(ValidationError, match="experiment"):
        DecisionMemo(
            decision_status="actionable",
            subproblem="排队透明度",
            **_memo_fields(),
        )

    with pytest.raises(ValidationError, match="subproblem"):
        DecisionMemo(
            decision_status="actionable",
            experiment=_experiment(),
            **_memo_fields(),
        )


def test_state_specific_memo_fields_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="only valid for"):
        DecisionMemo(
            decision_status="needs_evidence",
            evidence_plan=_evidence_plan(),
            experiment=_experiment(),
            **_memo_fields(),
        )

    with pytest.raises(ValidationError, match="refusal_reason"):
        DecisionMemo(decision_status="refused", **_memo_fields())


def test_each_decision_status_accepts_its_complete_shape() -> None:
    needs_evidence = DecisionMemo(
        decision_status="needs_evidence",
        evidence_plan=_evidence_plan(),
        **_memo_fields(),
    )
    actionable = DecisionMemo(
        decision_status="actionable",
        subproblem="排队透明度",
        experiment=_experiment(),
        **_memo_fields(),
    )
    refused = DecisionMemo(
        decision_status="refused",
        refusal_reason="可用证据不足，无法形成稳定信号。",
        **_memo_fields(),
    )

    assert needs_evidence.evidence_plan is not None
    assert actionable.experiment is not None
    assert refused.refusal_reason == "可用证据不足，无法形成稳定信号。"


def test_decision_memo_requires_an_explicit_completed_counter_check() -> None:
    unchecked = _memo_fields()
    unchecked["counter_evidence_checked"] = False
    with pytest.raises(ValidationError, match="counter_evidence_checked"):
        DecisionMemo(
            decision_status="needs_evidence",
            evidence_plan=_evidence_plan(),
            **unchecked,
        )

    missing = _memo_fields()
    missing.pop("counter_evidence_checked")
    with pytest.raises(ValidationError, match="counter_evidence_checked"):
        DecisionMemo(
            decision_status="needs_evidence",
            evidence_plan=_evidence_plan(),
            **missing,
        )

    unchecked_refusal = _memo_fields()
    unchecked_refusal["counter_evidence_checked"] = False
    refused = DecisionMemo(
        decision_status="refused",
        refusal_reason="COUNTER_EVIDENCE_NOT_CHECKED",
        **unchecked_refusal,
    )
    assert refused.counter_evidence_checked is False

    with pytest.raises(ValidationError, match="COUNTER_EVIDENCE_NOT_CHECKED"):
        DecisionMemo(
            decision_status="refused",
            refusal_reason="INSUFFICIENT_TOPIC_EVIDENCE",
            **unchecked_refusal,
        )


def test_memo_facts_reject_inconsistent_counts() -> None:
    with pytest.raises(ValidationError, match="negative_count"):
        MemoFacts(review_count=2, negative_count=3, negative_rate=75.0)


def test_generation_job_enforces_terminal_state_fields() -> None:
    timestamp = datetime(2026, 8, 10, 9, 0)

    with pytest.raises(ValidationError, match="memo_id"):
        MemoGenerationJob(
            id="job-1",
            dataset_version_id="v1",
            status="completed",
            created_at=timestamp,
            updated_at=timestamp,
        )

    with pytest.raises(ValidationError, match="error_code"):
        MemoGenerationJob(
            id="job-2",
            dataset_version_id="v1",
            status="failed",
            created_at=timestamp,
            updated_at=timestamp,
        )


def test_trace_supports_memo_specific_outcomes() -> None:
    timestamp = datetime(2026, 8, 10, 9, 0)

    for validation_status in ("numeric_mismatch", "failed"):
        trace = TraceRecord(
            id=f"trace-{validation_status}",
            entity_type="memo",
            entity_id="memo-1",
            dataset_version_id="v1",
            prompt_version="decision-memo-v1",
            provider="ollama",
            stage="grouping_evidence",
            retry_count=1,
            evidence_ids=["review-1"],
            validation_status=validation_status,
            latency_ms=12,
            token_estimate=20,
            created_at=timestamp,
        )
        assert trace.entity_type == "memo"
        assert trace.validation_status == validation_status
        assert trace.provider == "ollama"
        assert trace.stage == "grouping_evidence"
        assert trace.retry_count == 1


def test_trace_audit_fields_have_backward_compatible_defaults() -> None:
    trace = TraceRecord(
        id="trace-legacy",
        entity_type="insight",
        entity_id="insight-1",
        dataset_version_id="v1",
        prompt_version="insight-v1",
        validation_status="accepted",
        latency_ms=12,
        token_estimate=20,
        created_at=datetime(2026, 8, 10, 9, 0),
    )

    assert trace.provider is None
    assert trace.stage == "generation"
    assert trace.retry_count == 0
