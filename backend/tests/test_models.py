"""Tests for the stable domain contracts shared by later tasks."""

import pytest
from pydantic import ValidationError

from signalforge.core.errors import EvidenceInsufficientError
from signalforge.core.models import DatasetVersion, Insight


def test_insight_requires_at_least_one_evidence_id() -> None:
    version = DatasetVersion(id="asap-demo-v1", source_name="ASAP", row_count=3)
    insight = Insight(
        id="ins-1",
        dataset_version_id=version.id,
        title="服务响应慢",
        claim="用户反复提及等待时间过长。",
        evidence_ids=["review-1"],
        confidence=0.82,
    )

    assert insight.evidence_ids == ["review-1"]


def test_insight_rejects_empty_evidence_ids() -> None:
    with pytest.raises(ValidationError):
        Insight(
            id="ins-1",
            dataset_version_id="asap-demo-v1",
            title="服务响应慢",
            claim="缺少可验证依据的结论。",
            evidence_ids=[],
            confidence=0.82,
        )


def test_evidence_insufficient_error_retains_safe_context() -> None:
    error = EvidenceInsufficientError(evidence_count=1, required_count=2)

    assert str(error) == "证据不足，建议补充样本或调整筛选条件。"
    assert error.evidence_count == 1
    assert error.required_count == 2
