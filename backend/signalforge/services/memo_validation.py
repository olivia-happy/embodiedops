"""Deterministic safety gates for model-generated decision memos.

The local model may group language and draft prose, but it never owns evidence
identity, numeric facts, or the final decision state.  This module is the
boundary that enforces that separation before a memo can be persisted.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from signalforge.core.models import (
    DecisionMemo,
    DecisionStatus,
    Evidence,
    EvidencePlan,
    ExperimentPlan,
    MemoEvidenceReference,
    MemoFacts,
)
from signalforge.services.analytics import TopicMetric

SERVER_RULE_EXPERIMENT_DURATION_DAYS = 14
SERVER_RULE_EXPERIMENT_STOP_CONDITIONS = ("若护栏指标恶化则停止实验",)
_RULE_OWNED_OR_IDENTIFIER_FIELDS = frozenset(
    {
        "facts",
        "decision_status",
        "counter_evidence_checked",
        "evidence_id",
        "topic",
        "evidence_plan",
    }
)
_CHINESE_QUANTITY_PATTERN = re.compile(
    r"[零〇一二两三四五六七八九十百千万亿]+"
    r"(?:百分点|小时|分钟|分之|天|周|月|年|条|个|次|人|家|元|成|倍|率)"
)
_ENGLISH_NUMBER_WORDS = (
    r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|"
    r"million|billion"
)
_ENGLISH_QUANTITY_PATTERN = re.compile(
    rf"\b(?:{_ENGLISH_NUMBER_WORDS})(?:[\s-]+(?:{_ENGLISH_NUMBER_WORDS}))*"
    r"[\s-]+(?:percent|days?|weeks?|months?|years?|hours?|minutes?|times?)\b",
    re.IGNORECASE,
)
_ENGLISH_MULTIPLIER_PATTERN = re.compile(
    r"\b(?:double|triple|twice|half|quarters?|thirds?|fractions?)\b",
    re.IGNORECASE,
)


class MemoValidationError(ValueError):
    """A stable, machine-readable refusal from the final memo safety gate."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        del detail
        self.code = code
        self.detail: None = None
        super().__init__(code)


class _StrictModelOutput(BaseModel):
    """Base contract for untrusted structured output from the local model."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


class SubproblemDraft(_StrictModelOutput):
    """One model-proposed mechanism and its explicit evidence references."""

    name: str = Field(min_length=1, max_length=300)
    mechanism: str = Field(min_length=1, max_length=1000)
    supporting_evidence_ids: list[str] = Field(min_length=1, max_length=50)
    counter_evidence_ids: list[str] = Field(default_factory=list, max_length=50)
    missing_information: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> SubproblemDraft:
        cited = [*self.supporting_evidence_ids, *self.counter_evidence_ids]
        if len(cited) != len(set(cited)):
            raise ValueError("DUPLICATE_EVIDENCE_ID")
        return self

    def all_evidence_ids(self) -> set[str]:
        """Return every citation used by this candidate subproblem."""

        return {*self.supporting_evidence_ids, *self.counter_evidence_ids}


class GroupingDraft(_StrictModelOutput):
    """Strict first-stage model output before evidence validation."""

    subproblems: list[SubproblemDraft] = Field(min_length=1, max_length=10)
    counter_evidence_checked: bool

    def all_evidence_ids(self) -> set[str]:
        """Return every citation anywhere in the grouping."""

        return {
            evidence_id
            for subproblem in self.subproblems
            for evidence_id in subproblem.all_evidence_ids()
        }


class ValidatedGrouping(_StrictModelOutput):
    """Citation validation plus the candidates that meet the support threshold."""

    accepted: bool
    reason: str | None = None
    subproblems: list[SubproblemDraft] = Field(default_factory=list, max_length=10)
    validated_subproblems: list[SubproblemDraft] = Field(default_factory=list, max_length=10)
    counter_evidence_checked: bool = False

    @model_validator(mode="after")
    def acceptance_fields_are_consistent(self) -> ValidatedGrouping:
        if self.accepted and self.reason is not None:
            raise ValueError("accepted grouping cannot have a rejection reason")
        if not self.accepted:
            if self.reason is None:
                raise ValueError("rejected grouping requires a reason")
            if self.validated_subproblems:
                raise ValueError("rejected grouping cannot contain validated subproblems")
        return self

    def all_evidence_ids(self) -> set[str]:
        """Return every citation proposed by the parsed model grouping."""

        return {
            evidence_id
            for subproblem in self.subproblems
            for evidence_id in subproblem.all_evidence_ids()
        }


class _ExperimentDraft(_StrictModelOutput):
    """Model-authored experiment language with no numeric control fields."""

    hypothesis: str = Field(min_length=1, max_length=1000)
    target_segment: str = Field(min_length=1, max_length=300)
    intervention: str = Field(min_length=1, max_length=1000)
    primary_metric: str = Field(min_length=1, max_length=300)
    guardrail_metric: str = Field(min_length=1, max_length=300)


class MemoDraft(_StrictModelOutput):
    """Allowed fields in the model-authored prose layer of a memo."""

    decision_statement: str = Field(min_length=1, max_length=1000)
    topic: str = Field(min_length=1, max_length=160)
    subproblem: str | None = Field(default=None, min_length=1, max_length=300)
    supporting_evidence: list[MemoEvidenceReference] = Field(max_length=50)
    counter_evidence: list[MemoEvidenceReference] = Field(max_length=50)
    unknowns: list[str] = Field(min_length=1, max_length=20)
    reasoning_summary: str = Field(min_length=1, max_length=2000)
    experiment: _ExperimentDraft | None = None
    evidence_plan: EvidencePlan | None = None
    refusal_reason: str | None = Field(default=None, min_length=1, max_length=1000)
    # These two fields are accepted only so they can be discarded.  Their
    # types and values deliberately cannot influence the authoritative output.
    facts: object | None = None
    decision_status: object | None = None
    counter_evidence_checked: object | None = None


def _text_contains_numeric_claim(text: str) -> bool:
    """Detect Unicode digits and explicit percentage notation in model prose."""

    return (
        any(
            character.isdigit() or unicodedata.category(character) == "No"
            for character in text
        )
        or "%" in text
        or "％" in text
        or "百分之" in text
        or _CHINESE_QUANTITY_PATTERN.search(text) is not None
        or any(marker in text for marker in ("翻倍", "减半", "过半", "一半"))
        or _ENGLISH_QUANTITY_PATTERN.search(text) is not None
        or _ENGLISH_MULTIPLIER_PATTERN.search(text) is not None
    )


def contains_model_numeric_claim(
    value: object,
    *,
    field_name: str | None = None,
) -> bool:
    if field_name in _RULE_OWNED_OR_IDENTIFIER_FIELDS:
        return False
    if isinstance(value, str):
        return _text_contains_numeric_claim(value)
    if isinstance(value, Mapping):
        return any(
            contains_model_numeric_claim(item, field_name=str(name))
            for name, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_model_numeric_claim(item) for item in value)
    return False


def grouping_response_schema() -> dict[str, object]:
    """Return the strict JSON Schema used for first-stage grouping."""

    return GroupingDraft.model_json_schema()


def memo_response_schema() -> dict[str, object]:
    """Return the strict JSON Schema used for actionable memo drafting."""

    return MemoDraft.model_json_schema()


def _rejected_grouping(
    reason: str,
    *,
    parsed: GroupingDraft | None = None,
    counter_evidence_checked: bool = False,
) -> ValidatedGrouping:
    return ValidatedGrouping(
        accepted=False,
        reason=reason,
        subproblems=[] if parsed is None else parsed.subproblems,
        validated_subproblems=[],
        counter_evidence_checked=counter_evidence_checked,
    )


def validate_grouping(
    draft: Mapping[str, object],
    evidence: list[Evidence],
    *,
    complete_evidence: list[Evidence] | None = None,
) -> ValidatedGrouping:
    """Validate the complete first-stage output against a scoped evidence set.

    Any unknown citation rejects the complete grouping.  Known redacted items
    remain traceable but never count toward the two-visible-support threshold.
    Duplicate citations are rejected instead of being silently deduplicated.
    """

    try:
        parsed = GroupingDraft.model_validate(draft, strict=True)
    except ValidationError as exc:
        duplicate = any(
            "DUPLICATE_EVIDENCE_ID" in error.get("msg", "")
            for error in exc.errors()
        )
        reason = "DUPLICATE_EVIDENCE_ID" if duplicate else "INVALID_GROUPING_SCHEMA"
        return _rejected_grouping(reason)

    by_id = {item.id: item for item in evidence}
    if len(by_id) != len(evidence):
        return _rejected_grouping("AMBIGUOUS_EVIDENCE_ID", parsed=parsed)

    complete_scope = evidence if complete_evidence is None else complete_evidence
    complete_by_id = {item.id: item for item in complete_scope}
    if len(complete_by_id) != len(complete_scope):
        return _rejected_grouping("AMBIGUOUS_EVIDENCE_ID", parsed=parsed)

    if not parsed.all_evidence_ids().issubset(by_id):
        return _rejected_grouping("UNKNOWN_EVIDENCE_ID", parsed=parsed)

    visible_counter_ids = {
        item.id
        for item in complete_scope
        if not item.redacted and item.sentiment in {"positive", "neutral"}
    }
    grouped_counter_ids = {
        evidence_id
        for subproblem in parsed.subproblems
        for evidence_id in subproblem.counter_evidence_ids
    }
    counter_evidence_checked = visible_counter_ids.issubset(grouped_counter_ids)

    validated_subproblems = [
        subproblem
        for subproblem in parsed.subproblems
        if len(
            {
                evidence_id
                for evidence_id in subproblem.supporting_evidence_ids
                if (
                    not by_id[evidence_id].redacted
                    and by_id[evidence_id].sentiment == "negative"
                )
            }
        )
        >= 2
        and counter_evidence_checked
        and visible_counter_ids.issubset(subproblem.counter_evidence_ids)
    ]
    return ValidatedGrouping(
        accepted=True,
        reason=None,
        subproblems=parsed.subproblems,
        validated_subproblems=validated_subproblems,
        counter_evidence_checked=counter_evidence_checked,
    )


def resolve_decision_status(
    metric: TopicMetric, grouping: ValidatedGrouping
) -> DecisionStatus:
    """Compute the final state without consulting model-provided confidence."""

    if (
        not grouping.accepted
        or not grouping.counter_evidence_checked
        or metric.review_count < 3
        or not metric.scoreable
        or metric.negative_count <= 0
        or metric.negative_rate is None
        or metric.negative_rate <= 0
    ):
        return "refused"
    if grouping.validated_subproblems:
        return "actionable"
    return "needs_evidence"


def build_authoritative_facts(
    metric: TopicMetric, opportunity_score: float | None
) -> MemoFacts:
    """Build the only numeric facts allowed into a persisted decision memo."""

    missing_fields = list(dict.fromkeys(metric.missing_fields))
    scoreable = metric.scoreable
    if opportunity_score is None:
        missing_fields.append("opportunity_score")
        scoreable = False
    return MemoFacts(
        review_count=metric.review_count,
        negative_count=metric.negative_count,
        negative_rate=metric.negative_rate,
        average_rating=metric.average_rating,
        severity=metric.severity,
        affected=metric.affected,
        evidence=metric.evidence,
        opportunity_score=opportunity_score,
        scoreable=scoreable,
        missing_fields=missing_fields,
    )


def _citation_ids(references: list[MemoEvidenceReference]) -> list[str]:
    return [reference.evidence_id for reference in references]


def _validate_citations(
    parsed: MemoDraft,
    metric: TopicMetric,
    grouping: ValidatedGrouping,
    evidence: list[Evidence],
) -> tuple[set[str], set[str]]:
    scoped_by_id = {
        item.id: item
        for item in evidence
        if item.dataset_version_id == metric.dataset_version_id
    }
    if len(scoped_by_id) != len(
        [item for item in evidence if item.dataset_version_id == metric.dataset_version_id]
    ):
        raise MemoValidationError("AMBIGUOUS_EVIDENCE_ID")

    supporting_ids = _citation_ids(parsed.supporting_evidence)
    counter_ids = _citation_ids(parsed.counter_evidence)
    all_ids = [*supporting_ids, *counter_ids]
    if len(all_ids) != len(set(all_ids)):
        raise MemoValidationError("DUPLICATE_EVIDENCE_ID")
    if not set(all_ids).issubset(scoped_by_id):
        raise MemoValidationError("UNKNOWN_EVIDENCE_ID")
    if not set(all_ids).issubset(grouping.all_evidence_ids()):
        raise MemoValidationError("CITATION_OUTSIDE_GROUPING")
    return set(supporting_ids), set(counter_ids)


def _validate_actionable_citations(
    parsed: MemoDraft,
    grouping: ValidatedGrouping,
    supporting_ids: set[str],
    counter_ids: set[str],
) -> None:
    selected = next(
        (
            candidate
            for candidate in grouping.validated_subproblems
            if candidate.name == parsed.subproblem
        ),
        None,
    )
    if selected is None:
        raise MemoValidationError("UNVALIDATED_SUBPROBLEM")

    expected_supporting = set(selected.supporting_evidence_ids)
    expected_counter = set(selected.counter_evidence_ids)
    if not expected_supporting.issubset(supporting_ids):
        raise MemoValidationError("INCOMPLETE_SUPPORTING_EVIDENCE")
    if supporting_ids != expected_supporting:
        raise MemoValidationError("CITATION_OUTSIDE_SUBPROBLEM")
    if not expected_counter.issubset(counter_ids):
        raise MemoValidationError("MISSING_COUNTER_EVIDENCE")
    if counter_ids != expected_counter:
        raise MemoValidationError("CITATION_OUTSIDE_SUBPROBLEM")


def validate_memo_draft(
    draft: Mapping[str, object],
    *,
    memo_id: str,
    metric: TopicMetric,
    opportunity_score: float | None,
    grouping: ValidatedGrouping,
    evidence: list[Evidence],
    model_name: str | None,
    prompt_version: str,
) -> DecisionMemo:
    """Validate model prose, inject rule-owned facts, and compute final state.

    Model-provided ``facts`` and ``decision_status`` are parsed only as opaque
    values and then discarded.  The returned domain object is therefore built
    exclusively from current-version citations and deterministic rule output.
    """

    parsed: MemoDraft | None = None
    invalid_schema = False
    try:
        parsed = MemoDraft.model_validate(draft, strict=True)
    except ValidationError:
        invalid_schema = True
    if invalid_schema or parsed is None:
        raise MemoValidationError("INVALID_MEMO_SCHEMA")

    if contains_model_numeric_claim(draft):
        raise MemoValidationError("NUMERIC_CLAIM_IN_MODEL_TEXT")

    if parsed.topic != metric.aspect:
        raise MemoValidationError("TOPIC_MISMATCH")
    if not grouping.counter_evidence_checked:
        raise MemoValidationError("COUNTER_EVIDENCE_NOT_CHECKED")

    supporting_ids, counter_ids = _validate_citations(
        parsed, metric, grouping, evidence
    )
    status = resolve_decision_status(metric, grouping)
    if status == "actionable":
        _validate_actionable_citations(
            parsed,
            grouping,
            supporting_ids,
            counter_ids,
        )
    elif status == "needs_evidence":
        expected_counter = {
            evidence_id
            for subproblem in grouping.subproblems
            for evidence_id in subproblem.counter_evidence_ids
        }
        if not expected_counter.issubset(counter_ids):
            raise MemoValidationError("MISSING_COUNTER_EVIDENCE")

    memo: DecisionMemo | None = None
    invalid_state = False
    try:
        experiment = (
            ExperimentPlan(
                **parsed.experiment.model_dump(mode="python"),
                duration_days=SERVER_RULE_EXPERIMENT_DURATION_DAYS,
                stop_conditions=list(SERVER_RULE_EXPERIMENT_STOP_CONDITIONS),
            )
            if parsed.experiment is not None
            else None
        )
        memo = DecisionMemo(
            id=memo_id,
            dataset_version_id=metric.dataset_version_id,
            decision_status=status,
            decision_statement=parsed.decision_statement,
            topic=metric.aspect,
            subproblem=parsed.subproblem,
            facts=build_authoritative_facts(metric, opportunity_score),
            supporting_evidence=parsed.supporting_evidence,
            counter_evidence=parsed.counter_evidence,
            counter_evidence_checked=grouping.counter_evidence_checked,
            unknowns=parsed.unknowns,
            reasoning_summary=parsed.reasoning_summary,
            experiment=experiment,
            evidence_plan=parsed.evidence_plan,
            refusal_reason=parsed.refusal_reason,
            model_name=model_name,
            prompt_version=prompt_version,
        )
    except ValidationError:
        invalid_state = True
    if invalid_state or memo is None:
        raise MemoValidationError("INVALID_STATE_FIELDS")
    return memo
