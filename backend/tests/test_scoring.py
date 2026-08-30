"""Tests for deterministic, explainable opportunity and risk scores."""

from signalforge.services.scoring import (
    OpportunityInputs,
    RiskInputs,
    score_opportunity,
    score_risk,
)


def test_opportunity_score_has_explainable_contributions() -> None:
    score = score_opportunity(
        OpportunityInputs(
            affected=100,
            negativity=80,
            severity=70,
            business_fit=60,
            evidence=90,
        )
    )

    assert score.scoreable is True
    assert score.total == 82.0
    assert score.contributions == {
        "affected": 30.0,
        "negativity": 20.0,
        "severity": 14.0,
        "business_fit": 9.0,
        "evidence": 9.0,
    }


def test_missing_opportunity_inputs_are_not_treated_as_zero() -> None:
    score = score_opportunity(
        OpportunityInputs(
            affected=100,
            negativity=None,
            severity=70,
            business_fit=60,
            evidence=90,
        )
    )

    assert score.scoreable is False
    assert score.total is None
    assert score.contributions == {}
    assert score.missing_fields == ["negativity"]


def test_risk_score_uses_fixed_weights() -> None:
    score = score_risk(
        RiskInputs(impact=80, urgency=70, likelihood=60, evidence_quality=90)
    )

    assert score.scoreable is True
    assert score.total == 74.5
    assert score.contributions == {
        "impact": 28.0,
        "urgency": 21.0,
        "likelihood": 12.0,
        "evidence_quality": 13.5,
    }


def test_missing_risk_inputs_are_not_treated_as_zero() -> None:
    score = score_risk(
        RiskInputs(impact=80, urgency=70, likelihood=None, evidence_quality=90)
    )

    assert score.scoreable is False
    assert score.total is None
    assert score.missing_fields == ["likelihood"]
