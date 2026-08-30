"""Deterministic task and failure metrics for embodied episodes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from .models import Episode, FailureType
from .phase_analysis import PHASE_ORDER, PhaseName


class RateMetric(BaseModel):
    """A rate that retains its numerator and denominator for auditability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    rate: float | None = Field(default=None, ge=0, le=1)


class FailureCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failure_type: str = Field(min_length=1)
    count: int = Field(ge=0)


class PhaseFailureMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: PhaseName
    failure_count: int = Field(ge=0)
    denominator: int = Field(ge=0)
    failure_rate: float | None = Field(default=None, ge=0, le=1)


class TaskMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    success_rate: RateMetric
    grasp_success_rate: RateMetric
    placement_success_rate: RateMetric
    collision_rate: RateMetric
    timeout_rate: RateMetric
    mean_completion_time_s: float | None = Field(default=None, ge=0)
    per_phase_failure_rate: tuple[PhaseFailureMetric, ...]
    failure_distribution: tuple[FailureCount, ...]


def _rate(numerator: int, denominator: int) -> RateMetric:
    return RateMetric(
        numerator=numerator,
        denominator=denominator,
        rate=(numerator / denominator if denominator else None),
    )


def failure_type_distribution(episodes: Sequence[Episode]) -> list[FailureCount]:
    counts = Counter(
        episode.outcome.failure_type.value if episode.outcome.failure_type else "success"
        for episode in episodes
    )
    return [FailureCount(failure_type=key, count=counts[key]) for key in sorted(counts)]


_FAILURE_PHASE: dict[FailureType, PhaseName] = {
    FailureType.PLANNER_UNREACHABLE: "approach",
    FailureType.OCCLUSION: "align",
    FailureType.GRASP_MISS: "grasp",
    FailureType.COLLISION: "transfer",
    FailureType.TIMEOUT: "place",
    FailureType.END_EFFECTOR_MISALIGNMENT: "align",
    FailureType.GRIPPER_FORCE_INSUFFICIENT: "grasp",
}


def aggregate_task_metrics(episodes: Sequence[Episode]) -> TaskMetrics:
    """Aggregate metrics without dropping zero denominators.

    This first version has no separate placement-failure label, so grasp and
    placement success use the final task success as their conservative proxy.
    Their explicit denominators make that limitation visible to callers.
    """

    episode_count = len(episodes)
    success_count = sum(episode.outcome.success for episode in episodes)
    collision_count = sum(
        any(event.event_type == "collision" for event in episode.events)
        for episode in episodes
    )
    timeout_count = sum(
        any(event.event_type == "timeout" for event in episode.events)
        for episode in episodes
    )
    durations = [episode.outcome.completion_time_s for episode in episodes]
    phase_failures = Counter(
        _FAILURE_PHASE[episode.outcome.failure_type]
        for episode in episodes
        if episode.outcome.failure_type in _FAILURE_PHASE
    )
    per_phase = tuple(
        PhaseFailureMetric(
            phase=phase,
            failure_count=phase_failures[phase],
            denominator=episode_count,
            failure_rate=(phase_failures[phase] / episode_count if episode_count else None),
        )
        for phase in PHASE_ORDER
    )
    return TaskMetrics(
        episode_count=episode_count,
        success_count=success_count,
        success_rate=_rate(success_count, episode_count),
        grasp_success_rate=_rate(success_count, episode_count),
        placement_success_rate=_rate(success_count, episode_count),
        collision_rate=_rate(collision_count, episode_count),
        timeout_rate=_rate(timeout_count, episode_count),
        mean_completion_time_s=(sum(durations) / len(durations) if durations else None),
        per_phase_failure_rate=per_phase,
        failure_distribution=tuple(failure_type_distribution(episodes)),
    )

