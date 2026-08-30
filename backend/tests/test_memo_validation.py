"""Safety gates for evidence-bound decision memo generation."""

from collections.abc import Mapping
from dataclasses import replace

import pytest

from signalforge.core.models import Evidence
from signalforge.services.analytics import TopicMetric
from signalforge.services.memo_validation import (
    MemoValidationError,
    build_authoritative_facts,
    resolve_decision_status,
    validate_grouping,
    validate_memo_draft,
)


@pytest.fixture
def service_evidence() -> list[Evidence]:
    return [
        Evidence(
            id="review-1",
            dataset_version_id="v1",
            content="午高峰看不到排队进度。",
            rating=1,
            aspect="service",
            sentiment="negative",
        ),
        Evidence(
            id="review-2",
            dataset_version_id="v1",
            content="等待时没有预计完成时间。",
            rating=2,
            aspect="service",
            sentiment="negative",
        ),
        Evidence(
            id="review-redacted",
            dataset_version_id="v1",
            content="已脱敏内容",
            rating=1,
            aspect="service",
            sentiment="negative",
            redacted=True,
        ),
        Evidence(
            id="review-counter",
            dataset_version_id="v1",
            content="订单页的进度提示很清楚。",
            rating=5,
            aspect="service",
            sentiment="positive",
        ),
    ]


@pytest.fixture
def service_metric() -> TopicMetric:
    return TopicMetric(
        dataset_version_id="v1",
        aspect="service",
        review_count=4,
        negative_count=3,
        negative_rate=75.0,
        average_rating=2.0,
        severity=75.0,
        affected=80.0,
        evidence=40.0,
        scoreable=True,
    )


def _subproblem(
    *,
    name: str = "排队透明度",
    supporting_evidence_ids: list[str] | None = None,
    counter_evidence_ids: list[str] | None = None,
) -> dict[str, object]:
    counters = (
        ["review-counter"]
        if counter_evidence_ids is None
        else counter_evidence_ids
    )
    return {
        "name": name,
        "mechanism": "缺少进度信息会放大等待的不确定性。",
        "supporting_evidence_ids": supporting_evidence_ids or ["review-1", "review-2"],
        "counter_evidence_ids": counters,
        "missing_information": ["午高峰等待时长"],
    }


def _grouping(
    *subproblems: Mapping[str, object],
    counter_evidence_checked: bool = True,
) -> dict[str, object]:
    return {
        "subproblems": list(subproblems) or [_subproblem()],
        "counter_evidence_checked": counter_evidence_checked,
    }


def _actionable_draft() -> dict[str, object]:
    return {
        "decision_status": "needs_evidence",
        "counter_evidence_checked": False,
        "decision_statement": "先验证展示排队进度是否减少订单查询。",
        "topic": "service",
        "subproblem": "排队透明度",
        "facts": {
            "review_count": 999,
            "negative_count": 0,
            "negative_rate": 0.0,
            "invented_model_number": 12345,
        },
        "supporting_evidence": [
            {"evidence_id": "review-1", "rationale": "反映午高峰缺少排队进度。"},
            {"evidence_id": "review-2", "rationale": "反映缺少预计完成时间。"},
        ],
        "counter_evidence": [
            {
                "evidence_id": "review-counter",
                "rationale": "说明并非所有订单都缺少进度提示。",
            }
        ],
        "unknowns": ["尚不知道订单查询率是否会下降"],
        "reasoning_summary": "多条独立反馈支持该机制，同时存在相反体验。",
        "experiment": {
            "hypothesis": "展示排队位置和预计完成时间会减少订单进度查询。",
            "target_segment": "午高峰到店顾客",
            "intervention": "在订单页展示排队位置与预计完成时间。",
            "primary_metric": "订单进度查询率",
            "guardrail_metric": "订单取消率",
        },
    }


def _assert_safe_validation_error(error: MemoValidationError, sentinel: str) -> None:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    rendered: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        rendered.extend((str(current), repr(current), repr(vars(current))))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)

    assert len(seen) == 1
    assert error.__cause__ is None
    assert error.__context__ is None
    assert error.detail is None
    assert sentinel not in "\n".join(rendered)


def test_invalid_memo_schema_does_not_retain_pydantic_input(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    sentinel = "SENSITIVE-PYDANTIC-MODEL-PROSE"
    grouping = validate_grouping(_grouping(_subproblem()), service_evidence)
    draft = _actionable_draft()
    draft["unexpected_private_field"] = sentinel

    with pytest.raises(MemoValidationError) as captured:
        validate_memo_draft(
            draft,
            memo_id="memo-1",
            metric=service_metric,
            opportunity_score=71.5,
            grouping=grouping,
            evidence=service_evidence,
            model_name="local-model",
            prompt_version="decision-memo-v1",
        )

    assert captured.value.code == "INVALID_MEMO_SCHEMA"
    _assert_safe_validation_error(captured.value, sentinel)


def test_memo_validation_error_drops_untrusted_detail() -> None:
    sentinel = "SENSITIVE-UNTRUSTED-DETAIL"

    error = MemoValidationError("INVALID_MEMO_SCHEMA", sentinel)

    assert str(error) == "INVALID_MEMO_SCHEMA"
    assert error.detail is None
    assert sentinel not in repr(error)


def test_unknown_citation_rejects_whole_grouping(
    service_evidence: list[Evidence],
) -> None:
    draft = _grouping(
        _subproblem(supporting_evidence_ids=["review-1", "review-2"]),
        _subproblem(
            name="客服响应时效",
            supporting_evidence_ids=["review-1", "made-up-id"],
        ),
    )

    result = validate_grouping(draft, service_evidence)

    assert result.accepted is False
    assert result.reason == "UNKNOWN_EVIDENCE_ID"
    assert result.validated_subproblems == []


def test_redacted_evidence_cannot_satisfy_support_threshold(
    service_evidence: list[Evidence],
) -> None:
    result = validate_grouping(
        _grouping(
            _subproblem(
                supporting_evidence_ids=["review-1", "review-redacted"]
            )
        ),
        service_evidence,
    )

    assert result.accepted is True
    assert result.validated_subproblems == []


def test_duplicate_citation_is_rejected_instead_of_inflating_support(
    service_evidence: list[Evidence],
) -> None:
    result = validate_grouping(
        _grouping(
            _subproblem(supporting_evidence_ids=["review-1", "review-1"])
        ),
        service_evidence,
    )

    assert result.accepted is False
    assert result.reason == "DUPLICATE_EVIDENCE_ID"


def test_state_is_deterministic(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    dispersed = validate_grouping(
        _grouping(
            _subproblem(
                name="排队透明度",
                supporting_evidence_ids=["review-1"],
            ),
            _subproblem(
                name="客服响应时效",
                supporting_evidence_ids=["review-2"],
            ),
        ),
        service_evidence,
    )
    actionable = validate_grouping(_grouping(_subproblem()), service_evidence)
    self_reported_unchecked = validate_grouping(
        _grouping(_subproblem(), counter_evidence_checked=False),
        service_evidence,
    )
    too_small = TopicMetric(
        dataset_version_id="v1",
        aspect="service",
        review_count=2,
        negative_count=2,
        negative_rate=100.0,
        average_rating=1.5,
        severity=87.5,
        affected=40.0,
        evidence=20.0,
        scoreable=True,
    )

    assert resolve_decision_status(service_metric, dispersed) == "needs_evidence"
    assert resolve_decision_status(service_metric, actionable) == "actionable"
    assert self_reported_unchecked.counter_evidence_checked is True
    assert resolve_decision_status(service_metric, self_reported_unchecked) == "actionable"
    assert resolve_decision_status(too_small, actionable) == "refused"

    all_positive = TopicMetric(
        dataset_version_id="v1",
        aspect="service",
        review_count=4,
        negative_count=0,
        negative_rate=0.0,
        average_rating=5.0,
        severity=0.0,
        affected=80.0,
        evidence=40.0,
        scoreable=True,
    )
    assert resolve_decision_status(all_positive, actionable) == "refused"


def test_model_claimed_counter_check_cannot_hide_visible_counter_evidence(
    service_evidence: list[Evidence],
    service_metric: TopicMetric,
) -> None:
    result = validate_grouping(
        _grouping(
            _subproblem(counter_evidence_ids=[]),
            counter_evidence_checked=True,
        ),
        service_evidence,
    )

    assert result.accepted is True
    assert result.reason is None
    assert result.counter_evidence_checked is False
    assert resolve_decision_status(service_metric, result) == "refused"


def test_build_authoritative_facts_uses_only_metric_and_rule_score(
    service_metric: TopicMetric,
) -> None:
    facts = build_authoritative_facts(service_metric, opportunity_score=71.5)

    assert facts.model_dump() == {
        "review_count": 4,
        "negative_count": 3,
        "negative_rate": 75.0,
        "average_rating": 2.0,
        "severity": 75.0,
        "affected": 80.0,
        "evidence": 40.0,
        "opportunity_score": 71.5,
        "scoreable": True,
        "missing_fields": [],
    }


def test_memo_validation_overrides_model_numbers_and_state(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    grouping = validate_grouping(
        _grouping(
            _subproblem(counter_evidence_ids=["review-counter"]),
        ),
        service_evidence,
    )

    memo = validate_memo_draft(
        _actionable_draft(),
        memo_id="memo-1",
        metric=service_metric,
        opportunity_score=71.5,
        grouping=grouping,
        evidence=service_evidence,
        model_name="local-model",
        prompt_version="decision-memo-v1",
    )

    assert memo.decision_status == "actionable"
    assert memo.dataset_version_id == "v1"
    assert memo.facts.review_count == 4
    assert memo.facts.negative_count == 3
    assert memo.facts.negative_rate == 75.0
    assert memo.facts.opportunity_score == 71.5
    assert memo.counter_evidence_checked is True


@pytest.mark.parametrize(
    "numeric_claim",
    [
        "该改动预计让查询率下降 93%。",
        "该改动预计让查询率下降九成。",
        "用户可能需要等待三天。",
        "The result may improve by ninety percent.",
        "The result may improve twice.",
        "预计查询量减少一半。",
        "预计查询量减少⅓。",
    ],
)
def test_model_numeric_claims_in_memo_text_are_rejected(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
    numeric_claim: str,
) -> None:
    grouping = validate_grouping(
        _grouping(_subproblem(counter_evidence_ids=["review-counter"])),
        service_evidence,
    )
    draft = _actionable_draft()
    draft["decision_statement"] = numeric_claim

    with pytest.raises(MemoValidationError) as error:
        validate_memo_draft(
            draft,
            memo_id="memo-1",
            metric=service_metric,
            opportunity_score=71.5,
            grouping=grouping,
            evidence=service_evidence,
            model_name="local-model",
            prompt_version="decision-memo-v1",
        )

    assert error.value.code == "NUMERIC_CLAIM_IN_MODEL_TEXT"


def test_experiment_numeric_rules_are_injected_not_model_owned(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    grouping = validate_grouping(
        _grouping(_subproblem(counter_evidence_ids=["review-counter"])),
        service_evidence,
    )
    draft = _actionable_draft()
    memo = validate_memo_draft(
        draft,
        memo_id="memo-1",
        metric=service_metric,
        opportunity_score=71.5,
        grouping=grouping,
        evidence=service_evidence,
        model_name="local-model",
        prompt_version="decision-memo-v1",
    )

    assert memo.experiment is not None
    assert memo.experiment.duration_days == 14
    assert memo.experiment.stop_conditions == ["若护栏指标恶化则停止实验"]


def test_model_experiment_cannot_supply_server_numeric_control_fields(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    grouping = validate_grouping(
        _grouping(_subproblem(counter_evidence_ids=["review-counter"])),
        service_evidence,
    )
    draft = _actionable_draft()
    experiment = dict(draft["experiment"])
    experiment["duration_days"] = 30
    draft["experiment"] = experiment

    with pytest.raises(MemoValidationError) as error:
        validate_memo_draft(
            draft,
            memo_id="memo-1",
            metric=service_metric,
            opportunity_score=71.5,
            grouping=grouping,
            evidence=service_evidence,
            model_name="local-model",
            prompt_version="decision-memo-v1",
        )

    assert error.value.code == "INVALID_MEMO_SCHEMA"


def test_numeric_topic_is_allowed_because_topic_is_server_validated(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    metric = replace(service_metric, aspect="服务2.0")
    grouping = validate_grouping(
        _grouping(_subproblem(counter_evidence_ids=["review-counter"])),
        service_evidence,
    )
    draft = _actionable_draft()
    draft["topic"] = "服务2.0"

    memo = validate_memo_draft(
        draft,
        memo_id="memo-1",
        metric=metric,
        opportunity_score=71.5,
        grouping=grouping,
        evidence=service_evidence,
        model_name="local-model",
        prompt_version="decision-memo-v1",
    )

    assert memo.topic == "服务2.0"


def test_actionable_memo_must_expose_grouping_counter_evidence(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    grouping = validate_grouping(
        _grouping(_subproblem(counter_evidence_ids=["review-counter"])),
        service_evidence,
    )
    draft = _actionable_draft()
    draft["counter_evidence"] = []

    with pytest.raises(MemoValidationError) as error:
        validate_memo_draft(
            draft,
            memo_id="memo-1",
            metric=service_metric,
            opportunity_score=71.5,
            grouping=grouping,
            evidence=service_evidence,
            model_name="local-model",
            prompt_version="decision-memo-v1",
        )

    assert error.value.code == "MISSING_COUNTER_EVIDENCE"


def test_memo_requires_fields_for_computed_state(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    grouping = validate_grouping(
        _grouping(
            _subproblem(
                supporting_evidence_ids=["review-1"],
                counter_evidence_ids=["review-counter"],
            )
        ),
        service_evidence,
    )
    draft = _actionable_draft()
    draft["supporting_evidence"] = [
        {"evidence_id": "review-1", "rationale": "该候选问题目前支持证据不足。"}
    ]
    draft["evidence_plan"] = {
        "candidate_subproblems": ["排队透明度"],
        "collection_fields": ["午高峰等待时长"],
        "minimum_evidence_per_subproblem": 3,
        "reassessment_condition": "积累 3 条独立证据后复查。",
    }

    with pytest.raises(MemoValidationError) as error:
        validate_memo_draft(
            draft,
            memo_id="memo-1",
            metric=service_metric,
            opportunity_score=71.5,
            grouping=grouping,
            evidence=service_evidence,
            model_name="local-model",
            prompt_version="decision-memo-v1",
        )

    assert error.value.code == "INVALID_STATE_FIELDS"
    _assert_safe_validation_error(error.value, "积累 3 条独立证据后复查。")


def test_memo_citations_must_remain_in_validated_grouping(
    service_metric: TopicMetric,
    service_evidence: list[Evidence],
) -> None:
    grouping = validate_grouping(
        _grouping(_subproblem(counter_evidence_ids=["review-counter"])),
        service_evidence,
    )
    draft = _actionable_draft()
    supporting = list(draft["supporting_evidence"])
    supporting.append(
        {"evidence_id": "review-redacted", "rationale": "不应被新增到最终引用。"}
    )
    draft["supporting_evidence"] = supporting

    with pytest.raises(MemoValidationError) as error:
        validate_memo_draft(
            draft,
            memo_id="memo-1",
            metric=service_metric,
            opportunity_score=71.5,
            grouping=grouping,
            evidence=service_evidence,
            model_name="local-model",
            prompt_version="decision-memo-v1",
        )

    assert error.value.code == "CITATION_OUTSIDE_GROUPING"
