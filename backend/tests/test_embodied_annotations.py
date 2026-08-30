from __future__ import annotations

import pytest
from pydantic import ValidationError

from signalforge.embodied.annotations import (
    RealRobotDataCard,
    RootCauseAnnotation,
    validate_annotation,
)
from signalforge.embodied.models import Episode
from signalforge.embodied.simulator import generate_demo_episodes


@pytest.fixture
def episode() -> Episode:
    return generate_demo_episodes(
        count=6,
        seed=123,
        dataset_version_id="embodied-demo-v1",
    )[0]


def annotation_for(episode: Episode, **overrides: object) -> RootCauseAnnotation:
    event_id = episode.events[0].event_id
    values: dict[str, object] = {
        "annotation_id": "annotation-001",
        "dataset_version_id": episode.dataset_version_id,
        "episode_id": episode.episode_id,
        "failure_phase": "grasp",
        "root_cause": "The gripper did not retain contact after grasp.",
        "supporting_event_ids": [event_id],
        "counter_event_ids": [],
        "confidence": 0.82,
        "annotator_id": "reviewer-a",
        "review_status": "double_review",
        "reviewer_id": "reviewer-b",
        "notes": None,
    }
    values.update(overrides)
    return RootCauseAnnotation.model_validate(values)


def test_valid_double_review_annotation_is_bound_to_episode(episode: Episode) -> None:
    valid, code = validate_annotation(annotation_for(episode), episode)

    assert valid is True
    assert code is None


def test_annotation_rejects_unknown_event_id(episode: Episode) -> None:
    annotation = annotation_for(episode, supporting_event_ids=["not-an-episode-event"])

    valid, code = validate_annotation(annotation, episode)

    assert valid is False
    assert code == "UNKNOWN_EVENT_ID"


def test_annotation_rejects_dataset_version_mismatch(episode: Episode) -> None:
    annotation = annotation_for(episode, dataset_version_id="other-version")

    valid, code = validate_annotation(annotation, episode)

    assert valid is False
    assert code == "DATASET_VERSION_MISMATCH"


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("inf")])
def test_annotation_rejects_invalid_confidence(
    episode: Episode, confidence: float
) -> None:
    with pytest.raises(ValidationError):
        annotation_for(episode, confidence=confidence)


def test_validation_defends_against_bypassed_invalid_confidence(episode: Episode) -> None:
    values = annotation_for(episode).model_dump()
    values["confidence"] = float("inf")
    annotation = RootCauseAnnotation.model_construct(**values)

    valid, code = validate_annotation(annotation, episode)

    assert valid is False
    assert code == "INVALID_CONFIDENCE"


def test_adjudicated_annotation_requires_reviewer(episode: Episode) -> None:
    with pytest.raises(ValidationError, match="adjudicated annotations require"):
        annotation_for(
            episode,
            review_status="adjudicated",
            reviewer_id=None,
        )


def test_real_robot_card_requires_permission_metadata() -> None:
    with pytest.raises(ValidationError, match="license_or_permission"):
        RealRobotDataCard.model_validate(
            {
                "dataset_version_id": "real-export-v1",
                "source_name": "approved robot export",
                "real_robot_data": True,
                "robot_model": "arm6_gripper",
                "firmware": "v1.2.3",
                "task_family": "tabletop_pick_place",
                "license_or_permission": "",
                "redaction_status": "reviewed",
                "holdout_policy": "site-held-out",
            }
        )


def test_synthetic_data_card_cannot_claim_real_robot_data_without_permission() -> None:
    with pytest.raises(ValidationError, match="license_or_permission"):
        RealRobotDataCard.model_validate(
            {
                "dataset_version_id": "synthetic-v1",
                "source_name": "deterministic fixture",
                "real_robot_data": True,
                "robot_model": "arm6_gripper",
                "firmware": "sim-only",
                "task_family": "tabletop_pick_place",
                "license_or_permission": "   ",
                "redaction_status": "not_applicable",
                "holdout_policy": "synthetic split",
            }
        )
