"""Strict, version-scoped contracts for robot manipulation episodes.

The contracts deliberately contain only observations and events.  They do not
contain control commands or a robot connection, which keeps the demo fixture
safe to replay and inspect without a physical robot.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class FailureType(str, Enum):
    """Controlled vocabulary for deterministic failure injection."""

    GRASP_MISS = "grasp_miss"
    OCCLUSION = "occlusion"
    COLLISION = "collision"
    PLANNER_UNREACHABLE = "planner_unreachable"
    TIMEOUT = "timeout"
    END_EFFECTOR_MISALIGNMENT = "end_effector_misalignment"
    GRIPPER_FORCE_INSUFFICIENT = "gripper_force_insufficient"


class DatasetVersionRef(_StrictModel):
    """Provenance reference attached to an imported episode fixture."""

    dataset_version_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_uri: str | None = None
    file_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    schema_version: str = "embodied.episode.v1"


class EpisodeObservation(_StrictModel):
    """One synchronized robot observation at time ``t`` in seconds."""

    # Optional on the wire for compact clients; when present it is checked
    # against the parent Episode's immutable dataset version.
    dataset_version_id: str | None = Field(default=None, min_length=1)
    t: float = Field(ge=0)
    joint_positions: list[float] = Field(min_length=6, max_length=6)
    end_effector_pose: list[float] = Field(min_length=7, max_length=7)
    gripper_width: float = Field(ge=0)
    gripper_force: float | None = Field(default=None, ge=0)
    camera_frame_id: str = Field(min_length=1)

    @field_validator("joint_positions", "end_effector_pose")
    @classmethod
    def finite_values(cls, values: list[float]) -> list[float]:
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("observation vectors must contain finite values")
        return values

    @field_validator("gripper_width", "gripper_force")
    @classmethod
    def finite_scalar(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("observation scalars must contain finite values")
        return value


class EpisodeEvent(_StrictModel):
    """A bounded, typed event emitted by the simulator or normalizer."""

    dataset_version_id: str | None = Field(default=None, min_length=1)
    event_id: str = Field(min_length=1)
    t: float = Field(ge=0)
    event_type: Literal[
        "collision", "occlusion", "planner_error", "timeout", "grasp_contact"
    ]
    severity: Literal["info", "warning", "critical"]


class EpisodeOutcome(_StrictModel):
    """Deterministic task outcome; metrics are derived from this record."""

    success: bool
    failure_type: FailureType | None = None
    completion_time_s: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_failure_consistency(self) -> Self:
        if self.success and self.failure_type is not None:
            raise ValueError("successful episodes cannot have a failure_type")
        if not self.success and self.failure_type is None:
            raise ValueError("failed episodes require a failure_type")
        return self


class Episode(_StrictModel):
    """Immutable identity and synchronized records for one manipulation run."""

    episode_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    scene_id: str = Field(min_length=1)
    seed: int
    instruction: str = Field(min_length=1)
    robot_model: Literal["arm6_gripper"]
    observations: list[EpisodeObservation] = Field(min_length=1)
    events: list[EpisodeEvent] = Field(default_factory=list)
    outcome: EpisodeOutcome

    @model_validator(mode="after")
    def validate_episode_consistency(self) -> Self:
        observation_times = [observation.t for observation in self.observations]
        event_times = [event.t for event in self.events]
        if observation_times != sorted(observation_times):
            raise ValueError("observation timestamps must be monotonic")
        if event_times != sorted(event_times):
            raise ValueError("event timestamps must be monotonic")
        duration = observation_times[-1]
        if any(event.t > duration for event in self.events):
            raise ValueError("event timestamp exceeds episode duration")

        for observation in self.observations:
            if (
                observation.dataset_version_id is not None
                and observation.dataset_version_id != self.dataset_version_id
            ):
                raise ValueError("observation dataset_version_id must match episode")
        for event in self.events:
            if (
                event.dataset_version_id is not None
                and event.dataset_version_id != self.dataset_version_id
            ):
                raise ValueError("event dataset_version_id must match episode")
        return self
