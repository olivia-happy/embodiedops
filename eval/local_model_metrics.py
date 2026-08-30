"""Pure, privacy-safe metrics for live local-model evaluation.

This module never calls a model and never stores model-authored prose.  An
untrusted payload is converted immediately into booleans and integer counts so
raw model behavior can be reported separately from the validated system.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Literal

from pydantic import ValidationError

from signalforge.services.memo_validation import (
    GroupingDraft,
    MemoDraft,
    contains_model_numeric_claim,
)

EvaluationStage = Literal["grouping", "memo"]
TerminalStatus = Literal["actionable", "needs_evidence", "refused", "failed"]

_MECHANISM_MARKERS = (
    "因为",
    "由于",
    "导致",
    "使得",
    "从而",
    "环节",
    "路径",
    "触发",
    "缺少",
    "无法",
)
_CHINESE_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_NON_WORD = re.compile(r"[^\w\u3400-\u4dbf\u4e00-\u9fff]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class Ratio:
    """One rate with the facts needed to reproduce it."""

    numerator: int
    denominator: int
    rate: float | None


def ratio(numerator: int, denominator: int) -> Ratio:
    """Build a checked ratio; an empty population has an unknown rate."""

    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("invalid ratio")
    return Ratio(
        numerator=numerator,
        denominator=denominator,
        rate=None if denominator == 0 else round(numerator / denominator, 3),
    )


@dataclass(frozen=True, slots=True)
class RawAttemptAssessment:
    """Sanitized facts retained from exactly one provider attempt."""

    stage: EvaluationStage
    attempt_number: int
    json_object_received: bool
    domain_schema_valid: bool
    supporting_citation_count: int
    counter_citation_count: int
    unknown_citation_count: int
    visible_counter_evidence_count: int
    counter_evidence_omission_count: int
    numeric_text_detected: bool | None
    mechanism_rubric_passed: bool | None
    mechanism_count: int
    mechanism_pass_count: int
    error_code: str | None = None
    input_token_count: int | None = None
    generated_token_count: int | None = None
    model_total_duration_ns: int | None = None
    model_load_duration_ns: int | None = None
    input_evaluation_duration_ns: int | None = None
    generation_duration_ns: int | None = None
    model_tag_matched: bool | None = None

    @property
    def citation_count(self) -> int:
        return self.supporting_citation_count + self.counter_citation_count

    def to_dict(self) -> dict[str, object]:
        """Serialize only counters, booleans, timings, and stable codes."""

        result = asdict(self)
        result["citation_count"] = self.citation_count
        return result


@dataclass(frozen=True, slots=True)
class SystemRunAssessment:
    """Safety and integrity facts for one terminal orchestrator run."""

    terminal_status: TerminalStatus
    expected_status: Literal["actionable", "needs_evidence", "refused"]
    citation_count: int
    persisted_unknown_citation_count: int
    unsupported_numeric_claim_count: int
    redacted_reference_count: int
    evidence_link_pass_count: int
    dataset_version_pass_count: int


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return _NON_WORD.sub("", normalized)


def _mechanism_passes(
    mechanism: str,
    *,
    subproblem_name: str,
    evidence_texts: Collection[str],
) -> bool:
    normalized = _normalize_text(mechanism)
    if not normalized:
        return False
    comparisons = {
        _normalize_text(subproblem_name),
        *(_normalize_text(value) for value in evidence_texts),
    }
    chinese_count = len(_CHINESE_CHARACTER.findall(normalized))
    return (
        normalized not in comparisons
        and chinese_count >= 12
        and any(marker in mechanism for marker in _MECHANISM_MARKERS)
    )


def _invalid_assessment(
    stage: EvaluationStage,
    *,
    attempt_number: int,
    error_code: str | None = None,
    json_object_received: bool = True,
) -> RawAttemptAssessment:
    if attempt_number < 1:
        raise ValueError("attempt_number must be positive")
    return RawAttemptAssessment(
        stage=stage,
        attempt_number=attempt_number,
        json_object_received=json_object_received,
        domain_schema_valid=False,
        supporting_citation_count=0,
        counter_citation_count=0,
        unknown_citation_count=0,
        visible_counter_evidence_count=0,
        counter_evidence_omission_count=0,
        numeric_text_detected=None,
        mechanism_rubric_passed=None,
        mechanism_count=0,
        mechanism_pass_count=0,
        error_code=error_code,
    )


def failed_attempt_assessment(
    stage: EvaluationStage,
    *,
    attempt_number: int,
    error_code: str,
    model_tag_matched: bool | None = None,
) -> RawAttemptAssessment:
    """Represent a provider failure without retaining exception details."""

    return replace(
        _invalid_assessment(
            stage,
            attempt_number=attempt_number,
            error_code=error_code,
            json_object_received=False,
        ),
        model_tag_matched=model_tag_matched,
    )


def add_usage_metadata(
    assessment: RawAttemptAssessment,
    *,
    input_token_count: int | None,
    generated_token_count: int | None,
    model_total_duration_ns: int | None,
    model_load_duration_ns: int | None,
    input_evaluation_duration_ns: int | None,
    generation_duration_ns: int | None,
    model_tag_matched: bool | None,
) -> RawAttemptAssessment:
    """Attach numeric transport metadata to an immutable assessment."""

    return replace(
        assessment,
        input_token_count=input_token_count,
        generated_token_count=generated_token_count,
        model_total_duration_ns=model_total_duration_ns,
        model_load_duration_ns=model_load_duration_ns,
        input_evaluation_duration_ns=input_evaluation_duration_ns,
        generation_duration_ns=generation_duration_ns,
        model_tag_matched=model_tag_matched,
    )


def audit_grouping_payload(
    payload: object,
    *,
    allowed_evidence_ids: Collection[str],
    visible_counter_evidence_ids: Collection[str],
    evidence_text_by_id: Mapping[str, str],
    attempt_number: int = 1,
) -> RawAttemptAssessment:
    """Reduce a grouping payload to schema, citation, and mechanism counters."""

    if not isinstance(payload, Mapping):
        return _invalid_assessment(
            "grouping",
            attempt_number=attempt_number,
            json_object_received=False,
        )
    try:
        parsed = GroupingDraft.model_validate(payload, strict=True)
    except ValidationError:
        return _invalid_assessment(
            "grouping",
            attempt_number=attempt_number,
            error_code="INVALID_GROUPING_SCHEMA",
        )

    allowed = set(allowed_evidence_ids)
    visible_counter = set(visible_counter_evidence_ids)
    supporting = [
        evidence_id
        for subproblem in parsed.subproblems
        for evidence_id in subproblem.supporting_evidence_ids
    ]
    counter = [
        evidence_id
        for subproblem in parsed.subproblems
        for evidence_id in subproblem.counter_evidence_ids
    ]
    cited = [*supporting, *counter]
    cited_counter = set(counter)
    mechanism_passes = [
        _mechanism_passes(
            subproblem.mechanism,
            subproblem_name=subproblem.name,
            evidence_texts=evidence_text_by_id.values(),
        )
        for subproblem in parsed.subproblems
    ]
    prose_blocks = [
        {
            "name": subproblem.name,
            "mechanism": subproblem.mechanism,
            "missing_information": subproblem.missing_information,
        }
        for subproblem in parsed.subproblems
    ]
    return RawAttemptAssessment(
        stage="grouping",
        attempt_number=attempt_number,
        json_object_received=True,
        domain_schema_valid=True,
        supporting_citation_count=len(supporting),
        counter_citation_count=len(counter),
        unknown_citation_count=sum(item not in allowed for item in cited),
        visible_counter_evidence_count=len(visible_counter),
        counter_evidence_omission_count=len(visible_counter - cited_counter),
        numeric_text_detected=contains_model_numeric_claim(prose_blocks),
        mechanism_rubric_passed=all(mechanism_passes),
        mechanism_count=len(mechanism_passes),
        mechanism_pass_count=sum(mechanism_passes),
    )


def audit_memo_payload(
    payload: object,
    *,
    allowed_evidence_ids: Collection[str],
    expected_counter_evidence_ids: Collection[str],
    attempt_number: int = 1,
) -> RawAttemptAssessment:
    """Reduce a memo draft to strict-schema and citation safety counters."""

    if not isinstance(payload, Mapping):
        return _invalid_assessment(
            "memo",
            attempt_number=attempt_number,
            json_object_received=False,
        )
    try:
        parsed = MemoDraft.model_validate(payload, strict=True)
    except ValidationError:
        return _invalid_assessment(
            "memo",
            attempt_number=attempt_number,
            error_code="INVALID_MEMO_SCHEMA",
        )

    allowed = set(allowed_evidence_ids)
    expected_counter = set(expected_counter_evidence_ids)
    supporting = [item.evidence_id for item in parsed.supporting_evidence]
    counter = [item.evidence_id for item in parsed.counter_evidence]
    cited = [*supporting, *counter]
    return RawAttemptAssessment(
        stage="memo",
        attempt_number=attempt_number,
        json_object_received=True,
        domain_schema_valid=True,
        supporting_citation_count=len(supporting),
        counter_citation_count=len(counter),
        unknown_citation_count=sum(item not in allowed for item in cited),
        visible_counter_evidence_count=len(expected_counter),
        counter_evidence_omission_count=len(expected_counter - set(counter)),
        numeric_text_detected=contains_model_numeric_claim(
            parsed.model_dump(mode="python")
        ),
        mechanism_rubric_passed=None,
        mechanism_count=0,
        mechanism_pass_count=0,
    )


def _ratio_dict(numerator: int, denominator: int) -> dict[str, object]:
    return asdict(ratio(numerator, denominator))


def summarize_attempts(
    attempts: Sequence[RawAttemptAssessment],
) -> dict[str, object]:
    """Summarize raw provider behavior without using system outcomes."""

    first_passes = [item for item in attempts if item.attempt_number == 1]
    retries = [item for item in attempts if item.attempt_number == 2]
    citation_audits = [item for item in attempts if item.domain_schema_valid]
    numeric_audits = [
        item for item in attempts if item.numeric_text_detected is not None
    ]
    error_counts = Counter(
        item.error_code for item in attempts if item.error_code is not None
    )
    total_citations = sum(item.citation_count for item in citation_audits)
    visible_counters = sum(
        item.visible_counter_evidence_count for item in citation_audits
    )
    mechanism_count = sum(item.mechanism_count for item in attempts)
    model_match_audits = [
        item for item in attempts if item.model_tag_matched is not None
    ]
    return {
        "attempt_count": len(attempts),
        "stage_attempt_distribution": {
            "grouping": sum(item.stage == "grouping" for item in attempts),
            "memo": sum(item.stage == "memo" for item in attempts),
        },
        "error_code_counts": dict(sorted(error_counts.items())),
        "first_pass_json_object_rate": _ratio_dict(
            sum(item.json_object_received for item in first_passes),
            len(first_passes),
        ),
        "first_pass_domain_schema_rate": _ratio_dict(
            sum(item.domain_schema_valid for item in first_passes),
            len(first_passes),
        ),
        "after_one_retry_schema_rate": _ratio_dict(
            sum(item.domain_schema_valid for item in retries),
            len(retries),
        ),
        "unknown_citation_rate": _ratio_dict(
            sum(item.unknown_citation_count for item in citation_audits),
            total_citations,
        ),
        "visible_counter_omission_rate": _ratio_dict(
            sum(item.counter_evidence_omission_count for item in citation_audits),
            visible_counters,
        ),
        "numeric_text_block_rate": _ratio_dict(
            sum(item.numeric_text_detected is True for item in numeric_audits),
            len(numeric_audits),
        ),
        "mechanism_rubric_rate": _ratio_dict(
            sum(item.mechanism_pass_count for item in attempts),
            mechanism_count,
        ),
        "response_model_match_rate": _ratio_dict(
            sum(item.model_tag_matched is True for item in model_match_audits),
            len(model_match_audits),
        ),
    }


def summarize_system_runs(
    runs: Sequence[SystemRunAssessment],
) -> dict[str, object]:
    """Summarize validated artifacts independently from provider attempts."""

    terminal_distribution = {
        status: sum(item.terminal_status == status for item in runs)
        for status in ("actionable", "needs_evidence", "refused", "failed")
    }
    effective = [
        item for item in runs if item.terminal_status in {"actionable", "needs_evidence"}
    ]
    citation_count = sum(item.citation_count for item in runs)
    unknown_count = sum(item.persisted_unknown_citation_count for item in runs)
    unsupported_count = sum(item.unsupported_numeric_claim_count for item in runs)
    redacted_count = sum(item.redacted_reference_count for item in runs)
    return {
        "job_count": len(runs),
        "terminal_distribution": terminal_distribution,
        "effective_output_rate": _ratio_dict(len(effective), len(runs)),
        "expected_status_match_rate": _ratio_dict(
            sum(item.terminal_status == item.expected_status for item in runs),
            len(runs),
        ),
        "persisted_unknown_citation_count": unknown_count,
        "persisted_unknown_citation_rate": _ratio_dict(
            unknown_count, citation_count
        ),
        "unsupported_numeric_claim_count": unsupported_count,
        "unsupported_numeric_claim_rate": _ratio_dict(
            unsupported_count, len(effective)
        ),
        "redacted_reference_count": redacted_count,
        "redacted_reference_rate": _ratio_dict(redacted_count, citation_count),
        "evidence_link_integrity_rate": _ratio_dict(
            sum(item.evidence_link_pass_count for item in runs), citation_count
        ),
        "dataset_version_integrity_rate": _ratio_dict(
            sum(item.dataset_version_pass_count for item in runs), citation_count
        ),
    }


__all__ = [
    "RawAttemptAssessment",
    "Ratio",
    "SystemRunAssessment",
    "add_usage_metadata",
    "audit_grouping_payload",
    "audit_memo_payload",
    "failed_attempt_assessment",
    "ratio",
    "summarize_attempts",
    "summarize_system_runs",
]
