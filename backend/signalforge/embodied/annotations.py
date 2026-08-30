"""Approved-input contracts for root-cause annotation and robot data provenance.

These models intentionally define a review boundary only.  They neither import
robot exports nor invoke a robot, model provider, or persistence layer.
"""

from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from signalforge.embodied.models import Episode

AnnotationStatus = Literal[
    "unreviewed", "single_review", "double_review", "adjudicated"
]
FailurePhase = Literal["approach", "align", "grasp", "transfer", "place"]
RedactionStatus = Literal["not_applicable", "redacted", "reviewed"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RootCauseAnnotation(_StrictModel):
    """A human-provided, version-scoped explanation of one episode outcome."""

    annotation_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    failure_phase: FailurePhase
    root_cause: str = Field(min_length=1)
    supporting_event_ids: list[str] = Field(default_factory=list)
    counter_event_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    annotator_id: str = Field(min_length=1)
    review_status: AnnotationStatus
    reviewer_id: str | None = None
    notes: str | None = None

    @field_validator(
        "annotation_id",
        "dataset_version_id",
        "episode_id",
        "root_cause",
        "annotator_id",
        "reviewer_id",
        "notes",
    )
    @classmethod
    def text_fields_cannot_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("text fields cannot be blank")
        return value

    @field_validator("supporting_event_ids", "counter_event_ids")
    @classmethod
    def event_ids_cannot_be_blank(cls, event_ids: list[str]) -> list[str]:
        if any(not event_id.strip() for event_id in event_ids):
            raise ValueError("event IDs cannot be blank")
        return event_ids

    @field_validator("confidence")
    @classmethod
    def confidence_must_be_finite(cls, confidence: float) -> float:
        if not math.isfinite(confidence):
            raise ValueError("confidence must be finite")
        return confidence

    @model_validator(mode="after")
    def adjudication_requires_reviewer(self) -> Self:
        if self.review_status == "adjudicated" and self.reviewer_id is None:
            raise ValueError("adjudicated annotations require reviewer_id")
        return self


class RealRobotDataCard(_StrictModel):
    """Provenance gate required before a dataset may claim real robot data."""

    dataset_version_id: str = Field(min_length=1)
    source_name: str = Field(min_length=1)
    real_robot_data: bool
    robot_model: str = Field(min_length=1)
    firmware: str = Field(min_length=1)
    task_family: str = Field(min_length=1)
    license_or_permission: str = Field(min_length=1)
    redaction_status: RedactionStatus
    holdout_policy: str = Field(min_length=1)

    @field_validator(
        "dataset_version_id",
        "source_name",
        "robot_model",
        "firmware",
        "task_family",
        "license_or_permission",
        "holdout_policy",
    )
    @classmethod
    def required_text_cannot_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("required data-card fields cannot be blank")
        return value

    @model_validator(mode="after")
    def real_robot_claim_requires_reviewable_provenance(self) -> Self:
        if not self.real_robot_data:
            return self
        if self.redaction_status == "not_applicable":
            raise ValueError("real_robot_data requires a redaction status")
        if self.license_or_permission.strip().lower() in {
            "unknown",
            "not_applicable",
            "none",
        }:
            raise ValueError("real_robot_data requires license_or_permission")
        return self


def validate_annotation(
    annotation: RootCauseAnnotation, episode: Episode
) -> tuple[bool, str | None]:
    """Validate that an approved annotation can only cite its own episode.

    The return value deliberately uses stable machine-readable codes, allowing
    callers to surface an error without retaining unredacted source payloads.
    """

    if annotation.dataset_version_id != episode.dataset_version_id:
        return False, "DATASET_VERSION_MISMATCH"
    if annotation.episode_id != episode.episode_id:
        return False, "EPISODE_ID_MISMATCH"
    if not math.isfinite(annotation.confidence) or not 0 <= annotation.confidence <= 1:
        return False, "INVALID_CONFIDENCE"
    if annotation.review_status == "adjudicated" and annotation.reviewer_id is None:
        return False, "ADJUDICATED_REVIEWER_REQUIRED"

    episode_event_ids = {event.event_id for event in episode.events}
    referenced_event_ids = set(annotation.supporting_event_ids)
    referenced_event_ids.update(annotation.counter_event_ids)
    if not referenced_event_ids.issubset(episode_event_ids):
        return False, "UNKNOWN_EVENT_ID"
    return True, None
