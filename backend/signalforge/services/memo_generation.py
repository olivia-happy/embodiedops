"""Two-stage local-model orchestration for one validated decision memo.

DuckDB and deterministic scoring own topic selection and every numeric fact.
The injected local provider is used only to group evidence and, when an
evidence threshold is met, draft memo prose.  This module never persists a
memo; the asynchronous job layer owns that transaction after this function
returns successfully.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol
from uuid import uuid4

from signalforge.core.models import (
    DecisionMemo,
    Evidence,
    EvidencePlan,
    MemoEvidenceReference,
    MemoFacts,
    MemoJobStatus,
    TraceStage,
)
from signalforge.db.connection import Database
from signalforge.db.repositories import ValidationStatus, list_review_evidence_by_aspect
from signalforge.services.analytics import TopicMetric, list_topic_metrics
from signalforge.services.local_model import (
    INVALID_MODEL_JSON,
    LocalModelError,
)
from signalforge.services.memo_validation import (
    MemoValidationError,
    SubproblemDraft,
    ValidatedGrouping,
    build_authoritative_facts,
    grouping_response_schema,
    memo_response_schema,
    resolve_decision_status,
    validate_grouping,
    validate_memo_draft,
)
from signalforge.services.scoring import score_opportunity
from signalforge.services.tracing import (
    estimate_tokens,
    persist_memo_generation_trace,
)

PROMPT_VERSION = "decision-memo-v1"
GROUPING_PROMPT_VERSION = f"{PROMPT_VERSION}:grouping"
MEMO_PROMPT_VERSION = f"{PROMPT_VERSION}:draft"
DEFAULT_BUSINESS_FIT = 70.0
MAX_EVIDENCE_EXCERPT_CHARS = 320
MAX_GROUPING_EVIDENCE_ITEMS = 40
MINIMUM_EVIDENCE_PER_SUBPROBLEM = 3


GROUPING_SYSTEM = """Return JSON only. Do not include analysis or free text.
Group the supplied evidence into candidate subproblems. Use exactly these fields:
subproblems (array of name, mechanism, supporting_evidence_ids,
counter_evidence_ids, missing_information) and counter_evidence_checked (boolean).
Inspect every supplied positive or neutral item as possible counter-evidence.
Cite only supplied evidence_id values. Do not invent evidence, facts, or metrics."""

MEMO_SYSTEM = """Return JSON only. Do not include analysis or free text.
Draft one evidence-bound decision memo using exactly these prose and plan fields:
decision_statement, topic, subproblem, supporting_evidence, counter_evidence,
unknowns, reasoning_summary, experiment. Experiment must contain exactly:
hypothesis, target_segment, intervention, primary_metric, guardrail_metric.
Each evidence reference has evidence_id and rationale. Do not output
decision_status, confidence, dataset facts, duration, or stop conditions; the
server injects authoritative numbers and computes final state. Do not put
digits, quantities, or percentages in prose."""


class StructuredModelProvider(Protocol):
    """The local structured-generation boundary consumed by the orchestrator."""

    model_name: str | None

    async def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        response_schema: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        """Return one parsed JSON object or raise ``LocalModelError``."""


class MemoGenerationError(RuntimeError):
    """A stable orchestration/validation failure suitable for a failed job."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class _OpportunityCandidate:
    metric: TopicMetric
    score: float


def _model_name(provider: StructuredModelProvider) -> str | None:
    value = getattr(provider, "model_name", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _provider_name(provider: StructuredModelProvider) -> str | None:
    value = getattr(provider, "provider_name", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _select_top_complete_opportunity(
    db: Database, dataset_version_id: str
) -> _OpportunityCandidate:
    candidates: list[_OpportunityCandidate] = []
    for metric in list_topic_metrics(db, dataset_version_id):
        score = score_opportunity(
            metric.to_opportunity_inputs(business_fit=DEFAULT_BUSINESS_FIT)
        )
        if metric.scoreable and score.scoreable and score.total is not None:
            candidates.append(_OpportunityCandidate(metric=metric, score=score.total))
    if not candidates:
        raise MemoGenerationError("NO_COMPLETE_OPPORTUNITY")
    return sorted(
        candidates,
        key=lambda item: (
            -item.score,
            -item.metric.negative_count,
            -item.metric.review_count,
            item.metric.aspect,
        ),
    )[0]


def _visible_evidence_payload(evidence: list[Evidence]) -> list[dict[str, object]]:
    return [
        {
            "evidence_id": item.id,
            "aspect": item.aspect,
            "sentiment": item.sentiment,
            "rating": item.rating,
            "excerpt": item.content[:MAX_EVIDENCE_EXCERPT_CHARS],
        }
        for item in evidence
        if not item.redacted
    ]


def _representative_grouping_evidence(evidence: list[Evidence]) -> list[Evidence]:
    """Cap visible context while preserving support and counter-evidence buckets."""

    visible = [item for item in evidence if not item.redacted]
    if len(visible) <= MAX_GROUPING_EVIDENCE_ITEMS:
        return visible
    buckets = [
        [item for item in visible if item.sentiment == "negative"],
        [item for item in visible if item.sentiment in {"positive", "neutral"}],
        [item for item in visible if item.sentiment == "unknown"],
    ]
    positions = [0] * len(buckets)
    selected: list[Evidence] = []
    while len(selected) < MAX_GROUPING_EVIDENCE_ITEMS:
        progressed = False
        for index, bucket in enumerate(buckets):
            if positions[index] >= len(bucket):
                continue
            selected.append(bucket[positions[index]])
            positions[index] += 1
            progressed = True
            if len(selected) == MAX_GROUPING_EVIDENCE_ITEMS:
                break
        if not progressed:
            break
    return selected


def _grouping_prompt(
    candidate: _OpportunityCandidate,
    facts: MemoFacts,
    evidence: list[Evidence],
) -> str:
    payload = {
        "task": "Identify evidence-backed candidate subproblems for this one topic.",
        "dataset_version_id": candidate.metric.dataset_version_id,
        "topic": candidate.metric.aspect,
        "authoritative_facts": facts.model_dump(mode="json"),
        "allowed_evidence": _visible_evidence_payload(evidence),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _selected_subproblem(grouping: ValidatedGrouping) -> SubproblemDraft:
    return sorted(
        grouping.validated_subproblems,
        key=lambda item: (-len(item.supporting_evidence_ids), item.name),
    )[0]


def _memo_prompt(
    candidate: _OpportunityCandidate,
    facts: MemoFacts,
    grouping: ValidatedGrouping,
    evidence: list[Evidence],
) -> str:
    selected = _selected_subproblem(grouping)
    cited_ids = {
        *selected.supporting_evidence_ids,
        *selected.counter_evidence_ids,
    }
    payload = {
        "task": "Draft one falsifiable experiment memo for the validated subproblem.",
        "dataset_version_id": candidate.metric.dataset_version_id,
        "topic": candidate.metric.aspect,
        "validated_subproblem": selected.model_dump(mode="json"),
        "authoritative_facts_for_context_only": facts.model_dump(mode="json"),
        "allowed_evidence": _visible_evidence_payload(
            [item for item in evidence if item.id in cited_ids]
        ),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value.strip()))


def _grouping_references(
    grouping: ValidatedGrouping,
    evidence: list[Evidence],
) -> tuple[list[MemoEvidenceReference], list[MemoEvidenceReference]]:
    scoped_ids = {item.id for item in evidence}
    support_roles: dict[str, list[str]] = {}
    counter_roles: dict[str, list[str]] = {}
    for subproblem in grouping.subproblems:
        for evidence_id in subproblem.supporting_evidence_ids:
            if evidence_id in scoped_ids:
                support_roles.setdefault(evidence_id, []).append(subproblem.name)
        for evidence_id in subproblem.counter_evidence_ids:
            if evidence_id in scoped_ids and evidence_id not in support_roles:
                counter_roles.setdefault(evidence_id, []).append(subproblem.name)
    supporting = [
        MemoEvidenceReference(
            evidence_id=evidence_id,
            rationale=(f"支持候选子问题：{'、'.join(_unique(names))}")[:300],
        )
        for evidence_id, names in support_roles.items()
    ]
    counter = [
        MemoEvidenceReference(
            evidence_id=evidence_id,
            rationale=(f"限制候选子问题：{'、'.join(_unique(names))}")[:300],
        )
        for evidence_id, names in counter_roles.items()
        if evidence_id not in support_roles
    ]
    return supporting, counter


def _grouping_unknowns(grouping: ValidatedGrouping) -> list[str]:
    values = _unique(
        [
            value
            for subproblem in grouping.subproblems
            for value in subproblem.missing_information
        ]
    )
    return values[:20] or ["缺少区分候选子问题所需的人群、时间、渠道与结果字段"]


def _build_needs_evidence_memo(
    *,
    memo_id: str,
    candidate: _OpportunityCandidate,
    facts: MemoFacts,
    grouping: ValidatedGrouping,
    evidence: list[Evidence],
    model_name: str | None,
) -> DecisionMemo:
    supporting, counter = _grouping_references(grouping, evidence)
    candidates = _unique([item.name for item in grouping.subproblems])
    unknowns = _grouping_unknowns(grouping)
    return DecisionMemo(
        id=memo_id,
        dataset_version_id=candidate.metric.dataset_version_id,
        decision_status="needs_evidence",
        decision_statement=(
            f"{candidate.metric.aspect} 主题信号存在，但证据分散在不同问题机制上；"
            "暂不建议采取业务动作，先按补数计划区分首要子问题。"
        ),
        topic=candidate.metric.aspect,
        facts=facts,
        supporting_evidence=supporting,
        counter_evidence=counter,
        counter_evidence_checked=grouping.counter_evidence_checked,
        unknowns=unknowns,
        reasoning_summary=(
            f"已检查 {len(grouping.subproblems)} 个候选子问题及其反例，"
            "当前没有任何子问题达到至少两条非脱敏支持证据的行动门槛。"
        ),
        evidence_plan=EvidencePlan(
            candidate_subproblems=candidates,
            collection_fields=unknowns,
            minimum_evidence_per_subproblem=MINIMUM_EVIDENCE_PER_SUBPROBLEM,
            reassessment_condition=(
                "每个候选子问题积累至少 3 条独立、非脱敏证据，并补齐人群、"
                "时间、渠道和业务结果字段后重新评估。"
            ),
        ),
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
    )


def _refusal_reason(candidate: _OpportunityCandidate, grouping: ValidatedGrouping) -> str:
    if not grouping.counter_evidence_checked:
        return "COUNTER_EVIDENCE_NOT_CHECKED"
    if candidate.metric.review_count < 3:
        return "INSUFFICIENT_TOPIC_EVIDENCE"
    if not candidate.metric.scoreable:
        return "INCOMPLETE_TOPIC_FACTS"
    return "DECISION_GATE_NOT_MET"


def _build_refused_memo(
    *,
    memo_id: str,
    candidate: _OpportunityCandidate,
    facts: MemoFacts,
    grouping: ValidatedGrouping,
    evidence: list[Evidence],
    model_name: str | None,
) -> DecisionMemo:
    supporting, counter = _grouping_references(grouping, evidence)
    reason = _refusal_reason(candidate, grouping)
    return DecisionMemo(
        id=memo_id,
        dataset_version_id=candidate.metric.dataset_version_id,
        decision_status="refused",
        decision_statement="当前证据未通过确定性门槛，暂不形成业务结论。",
        topic=candidate.metric.aspect,
        facts=facts,
        supporting_evidence=supporting,
        counter_evidence=counter,
        counter_evidence_checked=grouping.counter_evidence_checked,
        unknowns=_grouping_unknowns(grouping),
        reasoning_summary=(
            "证据分组已完成，但主题样本、字段完整性或反例检查未满足结论门槛。"
        ),
        refusal_reason=reason,
        model_name=model_name,
        prompt_version=PROMPT_VERSION,
    )


def _numeric_mismatch(draft: Mapping[str, object], facts: MemoFacts) -> bool:
    model_facts = draft.get("facts")
    if not isinstance(model_facts, Mapping):
        return False
    authoritative = facts.model_dump(mode="python")
    for name, value in model_facts.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if name not in authoritative or authoritative[name] != value:
            return True
    return False


def _elapsed_ms(started_at: float) -> int:
    return round((perf_counter() - started_at) * 1000)


def _trace(
    db: Database,
    *,
    memo_id: str,
    dataset_version_id: str,
    prompt_version: str,
    evidence_ids: list[str],
    validation_status: ValidationStatus,
    started_at: float,
    token_estimate: int,
    model_name: str | None,
    provider_name: str | None,
    stage: TraceStage,
    retry_count: int,
) -> None:
    persist_memo_generation_trace(
        db,
        memo_id=memo_id,
        dataset_version_id=dataset_version_id,
        prompt_version=prompt_version,
        evidence_ids=_unique(evidence_ids),
        validation_status=validation_status,
        latency_ms=_elapsed_ms(started_at),
        token_estimate=token_estimate,
        model_name=model_name,
        provider=provider_name,
        stage=stage,
        retry_count=retry_count,
    )


def _can_retry_model_error(error: LocalModelError) -> bool:
    return error.code == INVALID_MODEL_JSON


async def _generate_grouping(
    *,
    db: Database,
    memo_id: str,
    dataset_version_id: str,
    provider: StructuredModelProvider,
    system_prompt: str,
    user_prompt: str,
    evidence: list[Evidence],
    complete_evidence: list[Evidence],
    on_stage: Callable[[MemoJobStatus], None],
) -> tuple[ValidatedGrouping, int]:
    evidence_ids = [item.id for item in evidence]
    token_estimate = estimate_tokens(system_prompt, user_prompt)
    model_name = _model_name(provider)
    provider_name = _provider_name(provider)
    validation_started = False
    for attempt in range(2):
        attempt_started_at = perf_counter()
        raw: Mapping[str, object] | None = None
        terminal_model_error_code: str | None = None
        provider_failed = False
        try:
            raw = await provider.generate_json(
                system_prompt,
                user_prompt,
                response_schema=grouping_response_schema(),
            )
        except LocalModelError as error:
            if attempt == 0 and _can_retry_model_error(error):
                _trace(
                    db,
                    memo_id=memo_id,
                    dataset_version_id=dataset_version_id,
                    prompt_version=GROUPING_PROMPT_VERSION,
                    evidence_ids=evidence_ids,
                    validation_status="retried",
                    started_at=attempt_started_at,
                    token_estimate=token_estimate,
                    model_name=model_name,
                    provider_name=provider_name,
                    stage="grouping_evidence",
                    retry_count=attempt + 1,
                )
                continue
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=dataset_version_id,
                prompt_version=GROUPING_PROMPT_VERSION,
                evidence_ids=evidence_ids,
                validation_status="failed",
                started_at=attempt_started_at,
                token_estimate=token_estimate,
                model_name=model_name,
                provider_name=provider_name,
                stage="grouping_evidence",
                retry_count=attempt,
            )
            terminal_model_error_code = error.code
        except Exception:
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=dataset_version_id,
                prompt_version=GROUPING_PROMPT_VERSION,
                evidence_ids=evidence_ids,
                validation_status="failed",
                started_at=attempt_started_at,
                token_estimate=token_estimate,
                model_name=model_name,
                provider_name=provider_name,
                stage="grouping_evidence",
                retry_count=attempt,
            )
            provider_failed = True

        if terminal_model_error_code is not None:
            raise LocalModelError(terminal_model_error_code)
        if provider_failed:
            raise MemoGenerationError("MODEL_PROVIDER_ERROR")
        assert raw is not None

        _trace(
            db,
            memo_id=memo_id,
            dataset_version_id=dataset_version_id,
            prompt_version=GROUPING_PROMPT_VERSION,
            evidence_ids=evidence_ids,
            validation_status="accepted",
            started_at=attempt_started_at,
            token_estimate=token_estimate,
            model_name=model_name,
            provider_name=provider_name,
            stage="grouping_evidence",
            retry_count=attempt,
        )

        if not validation_started:
            on_stage("validating_evidence")
            validation_started = True
        validation_started_at = perf_counter()
        grouping = validate_grouping(
            raw,
            evidence,
            complete_evidence=complete_evidence,
        )
        if grouping.accepted:
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=dataset_version_id,
                prompt_version=GROUPING_PROMPT_VERSION,
                evidence_ids=sorted(grouping.all_evidence_ids()),
                validation_status="accepted",
                started_at=validation_started_at,
                token_estimate=0,
                model_name=model_name,
                provider_name=provider_name,
                stage="validating_evidence",
                retry_count=attempt,
            )
            return grouping, attempt
        if attempt == 0 and grouping.reason == "INVALID_GROUPING_SCHEMA":
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=dataset_version_id,
                prompt_version=GROUPING_PROMPT_VERSION,
                evidence_ids=evidence_ids,
                validation_status="retried",
                started_at=validation_started_at,
                token_estimate=0,
                model_name=model_name,
                provider_name=provider_name,
                stage="validating_evidence",
                retry_count=attempt + 1,
            )
            continue
        _trace(
            db,
            memo_id=memo_id,
            dataset_version_id=dataset_version_id,
            prompt_version=GROUPING_PROMPT_VERSION,
            evidence_ids=evidence_ids,
            validation_status="invalid",
            started_at=validation_started_at,
            token_estimate=0,
            model_name=model_name,
            provider_name=provider_name,
            stage="validating_evidence",
            retry_count=attempt,
        )
        raise MemoGenerationError(grouping.reason or "INVALID_GROUPING")
    raise AssertionError("two-attempt grouping loop exhausted")


async def _generate_actionable_memo(
    *,
    db: Database,
    memo_id: str,
    candidate: _OpportunityCandidate,
    provider: StructuredModelProvider,
    facts: MemoFacts,
    grouping: ValidatedGrouping,
    evidence: list[Evidence],
) -> DecisionMemo:
    system_prompt = MEMO_SYSTEM
    user_prompt = _memo_prompt(candidate, facts, grouping, evidence)
    token_estimate = estimate_tokens(system_prompt, user_prompt)
    evidence_ids = sorted(grouping.all_evidence_ids())
    model_name = _model_name(provider)
    provider_name = _provider_name(provider)
    selected = _selected_subproblem(grouping)
    selected_grouping = grouping.model_copy(
        update={"validated_subproblems": [selected]}
    )
    for attempt in range(2):
        attempt_started_at = perf_counter()
        raw: Mapping[str, object] | None = None
        terminal_model_error_code: str | None = None
        provider_failed = False
        try:
            raw = await provider.generate_json(
                system_prompt,
                user_prompt,
                response_schema=memo_response_schema(),
            )
        except LocalModelError as error:
            if attempt == 0 and _can_retry_model_error(error):
                _trace(
                    db,
                    memo_id=memo_id,
                    dataset_version_id=candidate.metric.dataset_version_id,
                    prompt_version=MEMO_PROMPT_VERSION,
                    evidence_ids=evidence_ids,
                    validation_status="retried",
                    started_at=attempt_started_at,
                    token_estimate=token_estimate,
                    model_name=model_name,
                    provider_name=provider_name,
                    stage="generating_memo",
                    retry_count=attempt + 1,
                )
                continue
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=candidate.metric.dataset_version_id,
                prompt_version=MEMO_PROMPT_VERSION,
                evidence_ids=evidence_ids,
                validation_status="failed",
                started_at=attempt_started_at,
                token_estimate=token_estimate,
                model_name=model_name,
                provider_name=provider_name,
                stage="generating_memo",
                retry_count=attempt,
            )
            terminal_model_error_code = error.code
        except Exception:
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=candidate.metric.dataset_version_id,
                prompt_version=MEMO_PROMPT_VERSION,
                evidence_ids=evidence_ids,
                validation_status="failed",
                started_at=attempt_started_at,
                token_estimate=token_estimate,
                model_name=model_name,
                provider_name=provider_name,
                stage="generating_memo",
                retry_count=attempt,
            )
            provider_failed = True

        if terminal_model_error_code is not None:
            raise LocalModelError(terminal_model_error_code)
        if provider_failed:
            raise MemoGenerationError("MODEL_PROVIDER_ERROR")
        assert raw is not None

        _trace(
            db,
            memo_id=memo_id,
            dataset_version_id=candidate.metric.dataset_version_id,
            prompt_version=MEMO_PROMPT_VERSION,
            evidence_ids=evidence_ids,
            validation_status="accepted",
            started_at=attempt_started_at,
            token_estimate=token_estimate,
            model_name=model_name,
            provider_name=provider_name,
            stage="generating_memo",
            retry_count=attempt,
        )

        validation_started_at = perf_counter()
        memo: DecisionMemo | None = None
        terminal_validation_code: str | None = None
        try:
            memo = validate_memo_draft(
                raw,
                memo_id=memo_id,
                metric=candidate.metric,
                opportunity_score=candidate.score,
                grouping=selected_grouping,
                evidence=evidence,
                model_name=model_name,
                prompt_version=PROMPT_VERSION,
            )
        except MemoValidationError as error:
            if attempt == 0 and error.code in {
                "INVALID_MEMO_SCHEMA",
                "INVALID_STATE_FIELDS",
                "NUMERIC_CLAIM_IN_MODEL_TEXT",
            }:
                _trace(
                    db,
                    memo_id=memo_id,
                    dataset_version_id=candidate.metric.dataset_version_id,
                    prompt_version=MEMO_PROMPT_VERSION,
                    evidence_ids=evidence_ids,
                    validation_status="retried",
                    started_at=validation_started_at,
                    token_estimate=0,
                    model_name=model_name,
                    provider_name=provider_name,
                    stage="validating_evidence",
                    retry_count=attempt + 1,
                )
                continue
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=candidate.metric.dataset_version_id,
                prompt_version=MEMO_PROMPT_VERSION,
                evidence_ids=evidence_ids,
                validation_status="invalid",
                started_at=validation_started_at,
                token_estimate=0,
                model_name=model_name,
                provider_name=provider_name,
                stage="validating_evidence",
                retry_count=attempt,
            )
            terminal_validation_code = error.code

        if terminal_validation_code is not None:
            raise MemoGenerationError(terminal_validation_code)
        assert memo is not None

        if _numeric_mismatch(raw, facts):
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=candidate.metric.dataset_version_id,
                prompt_version=MEMO_PROMPT_VERSION,
                evidence_ids=evidence_ids,
                validation_status="numeric_mismatch",
                started_at=validation_started_at,
                token_estimate=0,
                model_name=model_name,
                provider_name=provider_name,
                stage="validating_evidence",
                retry_count=attempt,
            )
        else:
            _trace(
                db,
                memo_id=memo_id,
                dataset_version_id=candidate.metric.dataset_version_id,
                prompt_version=MEMO_PROMPT_VERSION,
                evidence_ids=evidence_ids,
                validation_status="accepted",
                started_at=validation_started_at,
                token_estimate=0,
                model_name=model_name,
                provider_name=provider_name,
                stage="validating_evidence",
                retry_count=attempt,
            )
        return memo
    raise AssertionError("two-attempt memo loop exhausted")


async def generate_decision_memo(
    db: Database,
    dataset_version_id: str,
    provider: StructuredModelProvider,
    on_stage: Callable[[MemoJobStatus], None],
) -> DecisionMemo:
    """Generate one validated memo while reporting only stages that actually start."""

    memo_id = str(uuid4())
    analysis_started_at = perf_counter()
    model_name = _model_name(provider)
    provider_name = _provider_name(provider)
    on_stage("analyzing_signals")
    try:
        candidate = _select_top_complete_opportunity(db, dataset_version_id)
    except MemoGenerationError:
        _trace(
            db,
            memo_id=memo_id,
            dataset_version_id=dataset_version_id,
            prompt_version=GROUPING_PROMPT_VERSION,
            evidence_ids=[],
            validation_status="failed",
            started_at=analysis_started_at,
            token_estimate=0,
            model_name=model_name,
            provider_name=provider_name,
            stage="analyzing_signals",
            retry_count=0,
        )
        raise

    complete_evidence = [
        item
        for item in list_review_evidence_by_aspect(
            db,
            dataset_version_id,
            candidate.metric.aspect,
        )
        if item.dataset_version_id == dataset_version_id
    ]
    _trace(
        db,
        memo_id=memo_id,
        dataset_version_id=dataset_version_id,
        prompt_version=GROUPING_PROMPT_VERSION,
        evidence_ids=[item.id for item in complete_evidence],
        validation_status="accepted",
        started_at=analysis_started_at,
        token_estimate=0,
        model_name=model_name,
        provider_name=provider_name,
        stage="analyzing_signals",
        retry_count=0,
    )
    evidence = _representative_grouping_evidence(complete_evidence)
    facts = build_authoritative_facts(candidate.metric, candidate.score)
    grouping_user_prompt = _grouping_prompt(candidate, facts, evidence)

    on_stage("grouping_evidence")
    grouping, grouping_retry_count = await _generate_grouping(
        db=db,
        memo_id=memo_id,
        dataset_version_id=dataset_version_id,
        provider=provider,
        system_prompt=GROUPING_SYSTEM,
        user_prompt=grouping_user_prompt,
        evidence=evidence,
        complete_evidence=complete_evidence,
        on_stage=on_stage,
    )
    decision_started_at = perf_counter()
    status = resolve_decision_status(candidate.metric, grouping)
    if status == "needs_evidence":
        memo = _build_needs_evidence_memo(
            memo_id=memo_id,
            candidate=candidate,
            facts=facts,
            grouping=grouping,
            evidence=evidence,
            model_name=model_name,
        )
        return memo
    if status == "refused":
        memo = _build_refused_memo(
            memo_id=memo_id,
            candidate=candidate,
            facts=facts,
            grouping=grouping,
            evidence=evidence,
            model_name=model_name,
        )
        _trace(
            db,
            memo_id=memo_id,
            dataset_version_id=dataset_version_id,
            prompt_version=GROUPING_PROMPT_VERSION,
            evidence_ids=sorted(grouping.all_evidence_ids()),
            validation_status="refused",
            started_at=decision_started_at,
            token_estimate=0,
            model_name=model_name,
            provider_name=provider_name,
            stage="validating_evidence",
            retry_count=grouping_retry_count,
        )
        return memo

    on_stage("generating_memo")
    return await _generate_actionable_memo(
        db=db,
        memo_id=memo_id,
        candidate=candidate,
        provider=provider,
        facts=facts,
        grouping=grouping,
        evidence=evidence,
    )
