"""Strict contracts for evidence-bound embodied failure diagnosis.

The models in this module are deliberately smaller than a robot policy
interface.  They describe an explanation and a proposed simulation study; no
control command, sensor write, or actuator field is accepted.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .phase_analysis import PhaseName

ConfidenceBand = Literal["low", "medium", "high"]
DecisionStatus = Literal["actionable", "needs_evidence", "refused"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class RootCauseCandidate(_StrictModel):
    """A language-only candidate; evidence identity lives at the draft root."""

    cause: str = Field(min_length=1, max_length=160)
    mechanism: str = Field(min_length=1, max_length=500)


class EvidenceWindow(_StrictModel):
    """Optional event window supplied by a provider and checked server-side."""

    event_id: str = Field(min_length=1)
    start_t: float = Field(ge=0)
    end_t: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> EvidenceWindow:
        if not math.isfinite(self.start_t) or not math.isfinite(self.end_t):
            raise ValueError("evidence window bounds must be finite")
        if self.start_t >= self.end_t:
            raise ValueError("evidence window start_t must be less than end_t")
        return self


class EpisodeDiagnosisDraft(_StrictModel):
    """Allowlisted structured output accepted from a diagnosis provider."""

    failure_phase: PhaseName
    root_cause_candidates: list[RootCauseCandidate] = Field(min_length=1, max_length=3)
    supporting_event_ids: list[str] = Field(default_factory=list, max_length=20)
    counter_event_ids: list[str] = Field(default_factory=list, max_length=20)
    unknowns: list[str] = Field(default_factory=list, max_length=20)
    confidence_band: ConfidenceBand
    decision_status: DecisionStatus
    evidence_windows: list[EvidenceWindow] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_unique_evidence(self) -> EpisodeDiagnosisDraft:
        all_ids = [*self.supporting_event_ids, *self.counter_event_ids]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("duplicate event IDs are not allowed")
        return self


class ValidatedDiagnosis(_StrictModel):
    """A post-validation diagnosis safe to display or feed to experiment design."""

    accepted: bool
    code: str = Field(min_length=1)
    validation_code: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    failure_phase: PhaseName
    root_cause_candidates: tuple[RootCauseCandidate, ...] = ()
    supporting_event_ids: tuple[str, ...] = ()
    counter_event_ids: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    confidence_band: ConfidenceBand
    decision_status: DecisionStatus
    provider: str = Field(min_length=1)
    model_name: str | None = None
    retry_count: int = Field(default=0, ge=0, le=1)
    rules_version: str = "diagnosis_rules_v1"

    @property
    def diagnosis(self) -> ValidatedDiagnosis:
        """Compatibility accessor used by ``ValidationResult`` consumers."""

        return self


class ValidationResult(_StrictModel):
    """Stable validator result; invalid drafts never leak a partial diagnosis."""

    accepted: bool
    code: str = Field(min_length=1)
    diagnosis: ValidatedDiagnosis | None = None
    draft: EpisodeDiagnosisDraft | None = None

    @property
    def reason(self) -> str:
        return self.code

