"""Fail-closed, provider-bounded diagnosis orchestration for episodes."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping

from pydantic import ValidationError

from signalforge.services.local_model import INVALID_MODEL_JSON, LocalModelError
from signalforge.services.memo_generation import StructuredModelProvider

from .diagnosis_models import (
    EpisodeDiagnosisDraft,
    ValidatedDiagnosis,
    ValidationResult,
)
from .models import Episode, FailureType
from .phase_analysis import EpisodeFeatureView

DIAGNOSIS_RULES_VERSION = "diagnosis_rules_v1"
_ALLOWED_DRAFT_FIELDS = frozenset(EpisodeDiagnosisDraft.model_fields)
_SENSOR_KEY_RE = re.compile(r"sensor|joint|pose|gripper|camera", re.IGNORECASE)
_FAILURE_PHASES: dict[FailureType, str] = {
    FailureType.PLANNER_UNREACHABLE: "approach",
    FailureType.OCCLUSION: "align",
    FailureType.GRASP_MISS: "grasp",
    FailureType.GRIPPER_FORCE_INSUFFICIENT: "grasp",
    FailureType.END_EFFECTOR_MISALIGNMENT: "align",
    FailureType.COLLISION: "transfer",
    FailureType.TIMEOUT: "place",
}
_EVENT_FOR_FAILURE: dict[FailureType, str] = {
    FailureType.PLANNER_UNREACHABLE: "planner_error",
    FailureType.OCCLUSION: "occlusion",
    FailureType.GRASP_MISS: "grasp_contact",
    FailureType.GRIPPER_FORCE_INSUFFICIENT: "grasp_contact",
    FailureType.END_EFFECTOR_MISALIGNMENT: "grasp_contact",
    FailureType.COLLISION: "collision",
    FailureType.TIMEOUT: "timeout",
}


def _contains_number(value: object, *, field_name: str | None = None) -> bool:
    """Reject provider-supplied numeric facts while allowing IDs and enums."""

    if field_name in {
        "supporting_event_ids",
        "counter_event_ids",
        "event_id",
        "start_t",
        "end_t",
        "evidence_windows",
    }:
        return False
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return any(char.isdigit() for char in value)
    if isinstance(value, Mapping):
        return any(_contains_number(item, field_name=str(key)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_number(item) for item in value)
    return False


def _rejected(code: str) -> ValidationResult:
    return ValidationResult(accepted=False, code=code, diagnosis=None, draft=None)


def _episode_duration(episode: Episode) -> float:
    return float(episode.observations[-1].t)


def _provider_name(provider: StructuredModelProvider | None) -> str:
    if provider is None:
        return "none"
    value = getattr(provider, "provider_name", None)
    return value.strip() if isinstance(value, str) and value.strip() else "structured_provider"


def _model_name(provider: StructuredModelProvider | None) -> str | None:
    if provider is None:
        return None
    value = getattr(provider, "model_name", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _raw_draft_validation(
    draft: Mapping[str, object], *, allowed_event_ids: frozenset[str], episode: Episode
) -> ValidationResult:
    unknown_keys = set(draft) - _ALLOWED_DRAFT_FIELDS
    sensor_keys = [key for key in unknown_keys if _SENSOR_KEY_RE.search(str(key))]
    if sensor_keys:
        return _rejected("UNKNOWN_SENSOR_FIELD")
    if "failure_type" in unknown_keys:
        return _rejected("UNSUPPORTED_FAILURE_TYPE")
    if unknown_keys:
        return _rejected("INVALID_DIAGNOSIS_SCHEMA")
    raw_windows = draft.get("evidence_windows", [])
    if isinstance(raw_windows, list):
        for raw_window in raw_windows:
            if not isinstance(raw_window, Mapping):
                return _rejected("INVALID_DIAGNOSIS_SCHEMA")
            start_t = raw_window.get("start_t")
            end_t = raw_window.get("end_t")
            if (
                isinstance(start_t, (int, float))
                and not isinstance(start_t, bool)
                and isinstance(end_t, (int, float))
                and not isinstance(end_t, bool)
                and (
                    not math.isfinite(float(start_t))
                    or not math.isfinite(float(end_t))
                    or float(start_t) < 0
                    or float(end_t) > _episode_duration(episode)
                    or float(start_t) >= float(end_t)
                )
            ):
                return _rejected("OUT_OF_BOUNDS_TIME_WINDOW")
    try:
        parsed = EpisodeDiagnosisDraft.model_validate(draft, strict=True)
    except ValidationError as exc:
        messages = " ".join(str(error.get("msg", "")) for error in exc.errors())
        if "failure_phase" in messages:
            return _rejected("UNSUPPORTED_FAILURE_TYPE")
        if "duplicate event IDs" in messages:
            return _rejected("DUPLICATE_EVENT_ID")
        return _rejected("INVALID_DIAGNOSIS_SCHEMA")

    if _contains_number(draft):
        return _rejected("FABRICATED_NUMERIC_CLAIM")

    event_by_id = {event.event_id: event for event in episode.events}
    cited_ids = [*parsed.supporting_event_ids, *parsed.counter_event_ids]
    if any(
        event_id not in allowed_event_ids or event_id not in event_by_id
        for event_id in cited_ids
    ):
        return _rejected("UNKNOWN_EVENT_ID")

    duration = _episode_duration(episode)
    for window in parsed.evidence_windows:
        if (
            window.event_id not in allowed_event_ids
            or window.event_id not in event_by_id
        ):
            return _rejected("UNKNOWN_EVENT_ID")
        if window.start_t < 0 or window.end_t > duration:
            return _rejected("OUT_OF_BOUNDS_TIME_WINDOW")
        if (
            event_by_id[window.event_id].t < window.start_t
            or event_by_id[window.event_id].t > window.end_t
        ):
            return _rejected("OUT_OF_BOUNDS_TIME_WINDOW")

    failure_type = episode.outcome.failure_type
    if failure_type is None:
        return _rejected("UNSUPPORTED_FAILURE_TYPE")
    expected_phase = _FAILURE_PHASES.get(failure_type)
    if expected_phase is None:
        return _rejected("UNSUPPORTED_FAILURE_TYPE")
    if parsed.failure_phase != expected_phase:
        return _rejected("FAILURE_PHASE_MISMATCH")

    if not parsed.supporting_event_ids:
        return _rejected("EVIDENCE_INSUFFICIENT")
    if set(parsed.supporting_event_ids) & set(parsed.counter_event_ids):
        return _rejected("DUPLICATE_EVENT_ID")
    if not parsed.counter_event_ids:
        return _rejected("MISSING_COUNTER_EVIDENCE")

    expected_event_type = _EVENT_FOR_FAILURE[failure_type]
    if not any(
        event_by_id[event_id].event_type == expected_event_type
        for event_id in parsed.supporting_event_ids
    ):
        return _rejected("EVIDENCE_INSUFFICIENT")

    status = (
        "actionable"
        if parsed.supporting_event_ids and parsed.counter_event_ids
        else "needs_evidence"
    )
    diagnosis = ValidatedDiagnosis(
        accepted=True,
        code="VALIDATED",
        validation_code="VALIDATED",
        episode_id=episode.episode_id,
        dataset_version_id=episode.dataset_version_id,
        failure_phase=parsed.failure_phase,
        root_cause_candidates=tuple(parsed.root_cause_candidates),
        supporting_event_ids=tuple(parsed.supporting_event_ids),
        counter_event_ids=tuple(parsed.counter_event_ids),
        unknowns=tuple(parsed.unknowns),
        confidence_band=parsed.confidence_band,
        decision_status=status,
        provider="validator",
        model_name=None,
        rules_version=DIAGNOSIS_RULES_VERSION,
    )
    return ValidationResult(accepted=True, code="VALIDATED", diagnosis=diagnosis, draft=parsed)


def validate_diagnosis(
    draft: Mapping[str, object], *, allowed_event_ids: frozenset[str], episode: Episode
) -> ValidationResult:
    """Validate all citations and boundaries without accepting model-owned facts."""

    if not isinstance(draft, Mapping):
        return _rejected("INVALID_DIAGNOSIS_SCHEMA")
    return _raw_draft_validation(draft, allowed_event_ids=allowed_event_ids, episode=episode)


def _validated_from_draft(
    draft: EpisodeDiagnosisDraft,
    *,
    episode: Episode,
    provider: str,
    model_name: str | None,
    retry_count: int,
    code: str = "VALIDATED",
) -> ValidatedDiagnosis:
    # The final status is intentionally derived from evidence, not trusted
    # from the provider's decision_status field.
    status = (
        "actionable"
        if draft.supporting_event_ids and draft.counter_event_ids
        else "needs_evidence"
    )
    return ValidatedDiagnosis(
        accepted=True,
        code=code,
        validation_code=code,
        episode_id=episode.episode_id,
        dataset_version_id=episode.dataset_version_id,
        failure_phase=draft.failure_phase,
        root_cause_candidates=tuple(draft.root_cause_candidates),
        supporting_event_ids=tuple(draft.supporting_event_ids),
        counter_event_ids=tuple(draft.counter_event_ids),
        unknowns=tuple(draft.unknowns),
        confidence_band=draft.confidence_band,
        decision_status=status,
        provider=provider,
        model_name=model_name,
        retry_count=retry_count,
        rules_version=DIAGNOSIS_RULES_VERSION,
    )


def _needs_evidence_without_provider(
    episode: Episode, feature_view: EpisodeFeatureView
) -> ValidatedDiagnosis:
    failure_type = episode.outcome.failure_type
    phase = _FAILURE_PHASES.get(failure_type, "grasp") if failure_type else "grasp"
    return ValidatedDiagnosis(
        accepted=True,
        code="PROVIDER_NOT_CONFIGURED",
        validation_code="PROVIDER_NOT_CONFIGURED",
        episode_id=episode.episode_id,
        dataset_version_id=feature_view.dataset_version_id,
        failure_phase=phase,  # type: ignore[arg-type]
        confidence_band="low",
        decision_status="needs_evidence",
        provider="none",
        model_name=None,
        unknowns=("structured diagnosis provider is not configured",),
        rules_version=DIAGNOSIS_RULES_VERSION,
    )


def _provider_prompt(episode: Episode, feature_view: EpisodeFeatureView) -> tuple[str, str]:
    allowed_events = [
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "severity": event.severity,
            "t": event.t,
        }
        for event in episode.events[:20]
    ]
    summary = {
        "episode_id": episode.episode_id,
        "dataset_version_id": episode.dataset_version_id,
        "task_id": episode.task_id,
        "outcome": episode.outcome.model_dump(mode="json"),
        "feature_view": feature_view.model_dump(mode="json"),
        "allowed_event_ids": allowed_events,
    }
    system = (
        "Return JSON only using the supplied diagnosis schema. Cite only allowed event_id values. "
        "Do not output numbers, sensor names, commands, or robot control instructions. "
        "Evidence is insufficient when support or counter evidence is absent."
    )
    return system, json.dumps(summary, ensure_ascii=False, separators=(",", ":"))


async def diagnose_episode(
    *,
    episode: Episode,
    feature_view: EpisodeFeatureView,
    provider: StructuredModelProvider | None,
) -> ValidatedDiagnosis:
    """Run one bounded diagnosis; all provider failures fail closed."""

    if (
        feature_view.episode_id != episode.episode_id
        or feature_view.dataset_version_id != episode.dataset_version_id
    ):
        return ValidatedDiagnosis(
            accepted=False,
            code="VERSION_MISMATCH",
            validation_code="VERSION_MISMATCH",
            episode_id=episode.episode_id,
            dataset_version_id=episode.dataset_version_id,
            failure_phase="grasp",
            confidence_band="low",
            decision_status="refused",
            provider=_provider_name(provider),
            model_name=_model_name(provider),
        )
    if provider is None:
        return _needs_evidence_without_provider(episode, feature_view)

    system_prompt, user_prompt = _provider_prompt(episode, feature_view)
    retry_count = 0
    payload: Mapping[str, object] | None = None
    while retry_count <= 1:
        try:
            payload = await provider.generate_json(
                system_prompt,
                user_prompt,
                response_schema=EpisodeDiagnosisDraft.model_json_schema(),
            )
            break
        except LocalModelError as exc:
            if exc.code != INVALID_MODEL_JSON or retry_count >= 1:
                return ValidatedDiagnosis(
                    accepted=False,
                    code=exc.code,
                    validation_code=exc.code,
                    episode_id=episode.episode_id,
                    dataset_version_id=episode.dataset_version_id,
                    failure_phase=_FAILURE_PHASES.get(episode.outcome.failure_type, "grasp"),  # type: ignore[arg-type]
                    confidence_band="low",
                    decision_status="refused",
                    provider=_provider_name(provider),
                    model_name=_model_name(provider),
                    retry_count=retry_count,
                )
            retry_count += 1
        except Exception:
            return ValidatedDiagnosis(
                accepted=False,
                code="PROVIDER_ERROR",
                validation_code="PROVIDER_ERROR",
                episode_id=episode.episode_id,
                dataset_version_id=episode.dataset_version_id,
                failure_phase=_FAILURE_PHASES.get(
                    episode.outcome.failure_type, "grasp"
                ),  # type: ignore[arg-type]
                confidence_band="low",
                decision_status="refused",
                provider=_provider_name(provider),
                model_name=_model_name(provider),
                retry_count=retry_count,
            )
    if payload is None:
        return ValidatedDiagnosis(
            accepted=False,
            code="INVALID_MODEL_JSON",
            validation_code="INVALID_MODEL_JSON",
            episode_id=episode.episode_id,
            dataset_version_id=episode.dataset_version_id,
            failure_phase="grasp",
            confidence_band="low",
            decision_status="refused",
            provider=_provider_name(provider),
            model_name=_model_name(provider),
            retry_count=retry_count,
        )

    result = validate_diagnosis(
        payload,
        allowed_event_ids=frozenset(event.event_id for event in episode.events),
        episode=episode,
    )
    if not result.accepted or result.draft is None:
        failure_phase = _FAILURE_PHASES.get(episode.outcome.failure_type, "grasp")
        return ValidatedDiagnosis(
            accepted=False,
            code=result.code,
            validation_code=result.code,
            episode_id=episode.episode_id,
            dataset_version_id=episode.dataset_version_id,
            failure_phase=failure_phase,  # type: ignore[arg-type]
            confidence_band="low",
            decision_status="refused",
            provider=_provider_name(provider),
            model_name=_model_name(provider),
            retry_count=retry_count,
        )
    return _validated_from_draft(
        result.draft,
        episode=episode,
        provider=_provider_name(provider),
        model_name=_model_name(provider),
        retry_count=retry_count,
    )
