"""Pure, deterministic score calculations used by decision and risk cards.

No model output participates in these calculations.  Missing measurements
produce an explicitly unscoreable result so callers cannot accidentally rank
an incomplete card as though a missing value were zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

_OPPORTUNITY_WEIGHTS: Mapping[str, float] = {
    "affected": 0.30,
    "negativity": 0.25,
    "severity": 0.20,
    "business_fit": 0.15,
    "evidence": 0.10,
}
_RISK_WEIGHTS: Mapping[str, float] = {
    "impact": 0.35,
    "urgency": 0.30,
    "likelihood": 0.20,
    "evidence_quality": 0.15,
}


def _validate_score_input(name: str, value: float | int | None) -> None:
    if value is not None and not 0 <= float(value) <= 100:
        raise ValueError(f"{name} must be between 0 and 100 when provided")


@dataclass(frozen=True, slots=True)
class OpportunityInputs:
    """Normalized (0–100) measurements for an opportunity-card calculation."""

    affected: float | None
    negativity: float | None
    severity: float | None
    business_fit: float | None
    evidence: float | None

    def __post_init__(self) -> None:
        for name, value in self.as_dict().items():
            _validate_score_input(name, value)

    def as_dict(self) -> dict[str, float | None]:
        return {
            "affected": self.affected,
            "negativity": self.negativity,
            "severity": self.severity,
            "business_fit": self.business_fit,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class RiskInputs:
    """Normalized (0–100) measurements for a market-risk calculation."""

    impact: float | None
    urgency: float | None
    likelihood: float | None
    evidence_quality: float | None

    def __post_init__(self) -> None:
        for name, value in self.as_dict().items():
            _validate_score_input(name, value)

    def as_dict(self) -> dict[str, float | None]:
        return {
            "impact": self.impact,
            "urgency": self.urgency,
            "likelihood": self.likelihood,
            "evidence_quality": self.evidence_quality,
        }


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    """A score with its arithmetic and any missing inputs made explicit."""

    total: float | None
    contributions: dict[str, float] = field(default_factory=dict)
    scoreable: bool = False
    missing_fields: list[str] = field(default_factory=list)


def _score(values: Mapping[str, float | None], weights: Mapping[str, float]) -> ScoreBreakdown:
    """Apply fixed weights after rejecting incomplete inputs."""

    missing_fields = [name for name in weights if values.get(name) is None]
    if missing_fields:
        return ScoreBreakdown(
            total=None,
            contributions={},
            scoreable=False,
            missing_fields=missing_fields,
        )

    contributions = {
        name: round(float(values[name]) * weight, 1)  # value is checked above.
        for name, weight in weights.items()
    }
    return ScoreBreakdown(
        total=round(sum(contributions.values()), 1),
        contributions=contributions,
        scoreable=True,
        missing_fields=[],
    )


def score_opportunity(inputs: OpportunityInputs) -> ScoreBreakdown:
    """Score an opportunity using affected/negative/severity/fit/evidence weights."""

    return _score(inputs.as_dict(), _OPPORTUNITY_WEIGHTS)


def score_risk(inputs: RiskInputs) -> ScoreBreakdown:
    """Score a risk using the fixed impact/urgency/likelihood/evidence weights."""

    return _score(inputs.as_dict(), _RISK_WEIGHTS)
