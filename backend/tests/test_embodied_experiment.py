from __future__ import annotations

from signalforge.embodied.diagnosis import validate_diagnosis
from signalforge.embodied.experiment import build_reproduction_experiment
from signalforge.embodied.models import EpisodeEvent
from signalforge.embodied.simulator import generate_demo_episodes


def test_reproduction_experiment_contains_all_safety_fields():
    episode = generate_demo_episodes(count=1, seed=11, dataset_version_id="emb-v1")[0]
    episode = episode.model_copy(
        update={
            "events": [
                *episode.events,
                EpisodeEvent(
                    dataset_version_id=episode.dataset_version_id,
                    event_id=f"{episode.episode_id}-counter",
                    t=3.0,
                    event_type="occlusion",
                    severity="info",
                ),
            ]
        }
    )
    payload = {
        "failure_phase": "grasp",
        "root_cause_candidates": [
            {"cause": "grasp alignment", "mechanism": "contact was not established"}
        ],
        "supporting_event_ids": [episode.events[0].event_id],
        "counter_event_ids": [episode.events[1].event_id],
        "unknowns": ["camera visibility is not fully observed"],
        "confidence_band": "medium",
        "decision_status": "needs_evidence",
    }
    validated = validate_diagnosis(
        payload,
        allowed_event_ids=frozenset(event.event_id for event in episode.events),
        episode=episode,
    )
    assert validated.accepted
    experiment = build_reproduction_experiment(validated.diagnosis)
    assert experiment.hypothesis
    assert experiment.variable
    assert experiment.controlled_conditions
    assert experiment.minimum_sample_count >= 1
    assert experiment.primary_success_metric
    assert experiment.guardrail_metric
    assert experiment.stop_condition
    assert experiment.reassessment_rule
    assert experiment.execution_mode == "simulation_only"


def test_reproduction_experiment_is_not_robot_control():
    episode = generate_demo_episodes(count=1, seed=11, dataset_version_id="emb-v1")[0]
    episode = episode.model_copy(
        update={
            "events": [
                *episode.events,
                EpisodeEvent(
                    dataset_version_id=episode.dataset_version_id,
                    event_id=f"{episode.episode_id}-counter",
                    t=3.0,
                    event_type="occlusion",
                    severity="info",
                ),
            ]
        }
    )
    payload = {
        "failure_phase": "grasp",
        "root_cause_candidates": [
            {"cause": "grasp alignment", "mechanism": "contact was not established"}
        ],
        "supporting_event_ids": [episode.events[0].event_id],
        "counter_event_ids": [episode.events[1].event_id],
        "unknowns": [],
        "confidence_band": "low",
        "decision_status": "needs_evidence",
    }
    validated = validate_diagnosis(
        payload,
        allowed_event_ids=frozenset(event.event_id for event in episode.events),
        episode=episode,
    )
    experiment = build_reproduction_experiment(validated.diagnosis)
    assert not hasattr(experiment, "robot_command")
