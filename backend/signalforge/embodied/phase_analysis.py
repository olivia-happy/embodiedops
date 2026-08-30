"""Deterministic phase segmentation and feature views for manipulation episodes."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Episode

PhaseName = Literal["approach", "align", "grasp", "transfer", "place"]
PHASE_ORDER: tuple[PhaseName, ...] = ("approach", "align", "grasp", "transfer", "place")
RULE_VERSION = "deterministic_v1"


class PhaseWindow(BaseModel):
    """A bounded, non-overlapping phase window produced by deterministic rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str = Field(min_length=1)
    phase: PhaseName
    start_t: float = Field(ge=0)
    end_t: float = Field(gt=0)
    rule_name: str = Field(min_length=1)
    observation_indices: tuple[int, ...] = ()
    event_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_bounds(self) -> PhaseWindow:
        if not math.isfinite(self.start_t) or not math.isfinite(self.end_t):
            raise ValueError("phase bounds must be finite")
        if self.start_t >= self.end_t:
            raise ValueError("phase start_t must be less than end_t")
        return self


class PhaseFeature(BaseModel):
    """Numeric, model-independent feature summary for one phase."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    phase: PhaseName
    duration_s: float = Field(ge=0)
    observation_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    collision_count: int = Field(ge=0)
    timeout_count: int = Field(ge=0)
    gripper_width_start: float | None = Field(default=None, ge=0)
    gripper_width_end: float | None = Field(default=None, ge=0)
    gripper_width_delta: float | None = None
    displacement_m: float = Field(ge=0)


class EpisodeFeatureView(BaseModel):
    """Version-bound feature view passed to downstream diagnosis code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    rule_name: str = RULE_VERSION
    observation_count: int = Field(ge=0)
    event_count: int = Field(ge=0)
    phases: tuple[PhaseFeature, ...] = Field(
        min_length=len(PHASE_ORDER), max_length=len(PHASE_ORDER)
    )

    @model_validator(mode="after")
    def validate_phase_order(self) -> EpisodeFeatureView:
        if tuple(item.phase for item in self.phases) != PHASE_ORDER:
            raise ValueError("feature phases must follow deterministic phase order")
        return self


def _phase_for_time(t: float, *, boundaries: tuple[float, ...], index: int) -> int:
    """Return the half-open phase bucket for ``t`` (last bucket includes end)."""

    for phase_index in range(len(PHASE_ORDER)):
        start = boundaries[phase_index]
        end = boundaries[phase_index + 1]
        if (start <= t < end) or (phase_index == len(PHASE_ORDER) - 1 and t == end):
            return phase_index
    # Episode validation bounds events, but keep this helper defensive.
    return min(max(index, 0), len(PHASE_ORDER) - 1)


def segment_episode(episode: Episode) -> list[PhaseWindow]:
    """Split an episode into five equal time windows using ``deterministic_v1``.

    The boundaries are derived only from the synchronized timestamps.  Sensor
    fields and typed events are attached to the resulting windows; no model is
    consulted and no phase can extend outside the observation duration.
    """

    if not episode.observations:
        raise ValueError("episode must contain observations")
    duration = episode.observations[-1].t
    if duration <= 0:
        raise ValueError("episode duration must be positive")
    boundaries = tuple(duration * index / len(PHASE_ORDER) for index in range(len(PHASE_ORDER) + 1))
    windows: list[PhaseWindow] = []
    for index, phase in enumerate(PHASE_ORDER):
        start, end = boundaries[index], boundaries[index + 1]
        observation_indices = tuple(
            observation_index
            for observation_index, observation in enumerate(episode.observations)
            if (start <= observation.t < end)
            or (index == len(PHASE_ORDER) - 1 and observation.t == end)
        )
        event_ids = tuple(
            event.event_id
            for event in episode.events
            if _phase_for_time(event.t, boundaries=boundaries, index=index) == index
        )
        windows.append(
            PhaseWindow(
                episode_id=episode.episode_id,
                phase=phase,
                start_t=start,
                end_t=end,
                rule_name=RULE_VERSION,
                observation_indices=observation_indices,
                event_ids=event_ids,
            )
        )
    return windows


def _displacement(episode: Episode, indices: tuple[int, ...]) -> float:
    if len(indices) < 2:
        return 0.0
    first = episode.observations[indices[0]].end_effector_pose[:3]
    last = episode.observations[indices[-1]].end_effector_pose[:3]
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(last, first, strict=True)))


def extract_episode_features(
    episode: Episode, phases: Sequence[PhaseWindow]
) -> EpisodeFeatureView:
    """Extract bounded numeric features from a previously segmented episode."""

    if len(phases) != len(PHASE_ORDER):
        raise ValueError("exactly five phase windows are required")
    if tuple(window.phase for window in phases) != PHASE_ORDER:
        raise ValueError("phase windows must follow deterministic phase order")
    if any(window.episode_id != episode.episode_id for window in phases):
        raise ValueError("phase windows must belong to the same episode_id")
    duration = episode.observations[-1].t
    valid_event_ids = {event.event_id for event in episode.events}
    for window in phases:
        if window.start_t < 0 or window.end_t > duration:
            raise ValueError("phase window is outside episode duration")
        if any(
            index < 0 or index >= len(episode.observations)
            for index in window.observation_indices
        ):
            raise ValueError("phase window contains an invalid observation index")
        if any(event_id not in valid_event_ids for event_id in window.event_ids):
            raise ValueError("phase window contains an unknown event_id")
    phase_features: list[PhaseFeature] = []
    for window in phases:
        observations = [episode.observations[index] for index in window.observation_indices]
        events = [event for event in episode.events if event.event_id in window.event_ids]
        widths = [observation.gripper_width for observation in observations]
        start_width = widths[0] if widths else None
        end_width = widths[-1] if widths else None
        phase_features.append(
            PhaseFeature(
                phase=window.phase,
                duration_s=window.end_t - window.start_t,
                observation_count=len(observations),
                event_count=len(events),
                collision_count=sum(event.event_type == "collision" for event in events),
                timeout_count=sum(event.event_type == "timeout" for event in events),
                gripper_width_start=start_width,
                gripper_width_end=end_width,
                gripper_width_delta=(end_width - start_width if widths else None),
                displacement_m=_displacement(episode, window.observation_indices),
            )
        )
    return EpisodeFeatureView(
        episode_id=episode.episode_id,
        dataset_version_id=episode.dataset_version_id,
        rule_name=RULE_VERSION,
        observation_count=len(episode.observations),
        event_count=len(episode.events),
        phases=tuple(phase_features),
    )
