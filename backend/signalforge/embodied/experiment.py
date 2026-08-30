"""Deterministic simulation-only reproduction experiment drafts."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from .diagnosis_models import ValidatedDiagnosis


class ReproductionExperiment(BaseModel):
    """A human-reviewed study definition, never a robot command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment_id: str = Field(min_length=1)
    diagnosis_code: str = Field(min_length=1)
    episode_id: str = Field(min_length=1)
    dataset_version_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1, max_length=500)
    variable: str = Field(min_length=1, max_length=200)
    controlled_conditions: tuple[str, ...] = Field(min_length=1, max_length=20)
    minimum_sample_count: int = Field(ge=1, le=100_000)
    primary_success_metric: str = Field(min_length=1, max_length=160)
    guardrail_metric: str = Field(min_length=1, max_length=160)
    stop_condition: str = Field(min_length=1, max_length=300)
    reassessment_rule: str = Field(min_length=1, max_length=300)
    execution_mode: str = "simulation_only"
    human_owner: str = "human_review_required"

    @property
    def sample_count(self) -> int:
        return self.minimum_sample_count

    @property
    def primary_metric(self) -> str:
        return self.primary_success_metric


def build_reproduction_experiment(diagnosis: ValidatedDiagnosis) -> ReproductionExperiment:
    """Build a bounded experiment from a validated diagnosis only."""

    if not diagnosis.accepted:
        raise ValueError("cannot build an experiment from an unvalidated diagnosis")
    digest = hashlib.sha256(
        f"{diagnosis.dataset_version_id}:{diagnosis.episode_id}:{diagnosis.failure_phase}".encode()
    ).hexdigest()[:12]
    candidate = diagnosis.root_cause_candidates[0] if diagnosis.root_cause_candidates else None
    cause = candidate.cause if candidate else "the observed failure mechanism"
    mechanism = (
        candidate.mechanism
        if candidate
        else "the current episode lacks a provider-backed cause"
    )
    return ReproductionExperiment(
        experiment_id=f"repro-{digest}",
        diagnosis_code=diagnosis.validation_code,
        episode_id=diagnosis.episode_id,
        dataset_version_id=diagnosis.dataset_version_id,
        hypothesis=f"Reducing {cause} will improve {diagnosis.failure_phase} phase completion.",
        variable=f"Controlled intervention targeting {mechanism}",
        controlled_conditions=(
            "same task, scene and seed distribution",
            "same robot abstraction and observation schema",
            "simulation only; no actuator or deployment calls",
        ),
        minimum_sample_count=30,
        primary_success_metric="phase completion rate with explicit numerator and denominator",
        guardrail_metric="collision and timeout rate must not increase",
        stop_condition="stop if collision or timeout guardrail worsens in two consecutive checks",
        reassessment_rule=(
            "reassess the hypothesis after the minimum sample count and review "
            "failures by phase"
        ),
        execution_mode="simulation_only",
        human_owner="human_review_required",
    )
