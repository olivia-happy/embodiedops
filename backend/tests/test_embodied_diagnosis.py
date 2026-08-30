from __future__ import annotations

import asyncio

import pytest

from signalforge.embodied.diagnosis import diagnose_episode, validate_diagnosis
from signalforge.embodied.diagnosis_models import EpisodeDiagnosisDraft
from signalforge.embodied.phase_analysis import (
    extract_episode_features,
    segment_episode,
)
from signalforge.embodied.simulator import generate_demo_episodes


def _episode():
    episode = generate_demo_episodes(count=1, seed=11, dataset_version_id="emb-v1")[0]
    features = extract_episode_features(episode, segment_episode(episode))
    return episode, features


def _payload(episode):
    event_ids = [event.event_id for event in episode.events]
    return {
        "failure_phase": "grasp",
        "root_cause_candidates": [
            {"cause": "grasp alignment", "mechanism": "contact was not established"}
        ],
        "supporting_event_ids": event_ids[:1],
        "counter_event_ids": event_ids[1:2],
        "unknowns": ["camera visibility is not fully observed"],
        "confidence_band": "medium",
        "decision_status": "needs_evidence",
    }


def test_unknown_event_id_fails_closed():
    episode, _ = _episode()
    result = validate_diagnosis(
        {**_payload(episode), "supporting_event_ids": ["missing-event"]},
        allowed_event_ids=frozenset(event.event_id for event in episode.events),
        episode=episode,
    )
    assert result.accepted is False
    assert result.code == "UNKNOWN_EVENT_ID"


def test_unknown_sensor_field_fails_closed():
    episode, _ = _episode()
    result = validate_diagnosis(
        {**_payload(episode), "sensor_fields": ["joint_temperature"]},
        allowed_event_ids=frozenset(event.event_id for event in episode.events),
        episode=episode,
    )
    assert result.accepted is False
    assert result.code == "UNKNOWN_SENSOR_FIELD"


def test_out_of_bounds_evidence_window_fails_closed():
    episode, _ = _episode()
    result = validate_diagnosis(
        {
            **_payload(episode),
            "evidence_windows": [
                {"event_id": episode.events[0].event_id, "start_t": -1, "end_t": 1}
            ],
        },
        allowed_event_ids=frozenset(event.event_id for event in episode.events),
        episode=episode,
    )
    assert result.accepted is False
    assert result.code == "OUT_OF_BOUNDS_TIME_WINDOW"


def test_numeric_claim_and_missing_counter_evidence_are_rejected():
    episode, _ = _episode()
    numeric = validate_diagnosis(
        {**_payload(episode), "unknowns": ["failure happened after 2 seconds"]},
        allowed_event_ids=frozenset(event.event_id for event in episode.events),
        episode=episode,
    )
    assert numeric.accepted is False
    assert numeric.code == "FABRICATED_NUMERIC_CLAIM"

    missing_counter = validate_diagnosis(
        {**_payload(episode), "counter_event_ids": []},
        allowed_event_ids=frozenset(event.event_id for event in episode.events),
        episode=episode,
    )
    assert missing_counter.accepted is False
    assert missing_counter.code == "MISSING_COUNTER_EVIDENCE"


def test_schema_is_strict_for_unknown_fields():
    with pytest.raises(Exception):
        EpisodeDiagnosisDraft.model_validate({"unsupported": True})


def test_diagnose_without_provider_returns_needs_evidence_not_fake_ai():
    episode, features = _episode()
    diagnosis = asyncio.run(
        diagnose_episode(episode=episode, feature_view=features, provider=None)
    )
    assert diagnosis.accepted is True
    assert diagnosis.decision_status == "needs_evidence"
    assert diagnosis.provider == "none"
    assert diagnosis.validation_code == "PROVIDER_NOT_CONFIGURED"
