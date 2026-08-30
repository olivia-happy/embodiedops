from __future__ import annotations

import pytest
from pydantic import ValidationError

from signalforge.embodied.models import (
    DatasetVersionRef,
    Episode,
    EpisodeEvent,
    EpisodeObservation,
    FailureType,
)


def _observation(t: float, *, dataset_version_id: str | None = None) -> EpisodeObservation:
    payload = {
        "t": t,
        "joint_positions": [0.0] * 6,
        "end_effector_pose": [0.0, 0.0, 0.1, 0.0, 0.0, 0.0, 1.0],
        "gripper_width": 0.04,
        "camera_frame_id": f"frame-{int(t * 10):03d}",
    }
    if dataset_version_id is not None:
        payload["dataset_version_id"] = dataset_version_id
    return EpisodeObservation.model_validate(payload)


def _episode(**overrides: object) -> Episode:
    payload: dict[str, object] = {
        "episode_id": "ep-1",
        "dataset_version_id": "embodied-demo-v1",
        "task_id": "pick_red_cube",
        "scene_id": "tabletop-01",
        "seed": 7,
        "instruction": "Pick the red cube and place it in the target box.",
        "robot_model": "arm6_gripper",
        "observations": [_observation(0.0), _observation(1.0), _observation(2.0)],
        "events": [],
        "outcome": {
            "success": True,
            "failure_type": None,
            "completion_time_s": 2.0,
        },
    }
    payload.update(overrides)
    return Episode.model_validate(payload)


def test_dataset_version_ref_is_immutable_and_strict() -> None:
    reference = DatasetVersionRef(dataset_version_id="embodied-demo-v1", source="synthetic")
    assert reference.dataset_version_id == "embodied-demo-v1"
    with pytest.raises(ValidationError):
        DatasetVersionRef.model_validate(
            {"dataset_version_id": "v1", "source": "synthetic", "unexpected": True}
        )


def test_episode_rejects_mixed_dataset_versions() -> None:
    with pytest.raises(ValidationError, match="dataset_version_id"):
        _episode(
            observations=[
                _observation(0.0, dataset_version_id="embodied-demo-v1"),
                _observation(1.0, dataset_version_id="other-version"),
            ]
        )


def test_episode_requires_monotonic_timestamps_and_known_failure_type() -> None:
    with pytest.raises(ValidationError, match="monotonic"):
        _episode(observations=[_observation(1.0), _observation(0.5)])

    with pytest.raises(ValidationError):
        _episode(
            outcome={
                "success": False,
                "failure_type": "not_a_supported_failure",
                "completion_time_s": 1.0,
            }
        )


def test_episode_rejects_failed_outcome_without_failure_type() -> None:
    with pytest.raises(ValidationError, match="failure_type"):
        _episode(
            outcome={"success": False, "failure_type": None, "completion_time_s": 1.0}
        )


def test_episode_rejects_event_outside_observation_time_range() -> None:
    event = EpisodeEvent(
        event_id="event-1",
        dataset_version_id="embodied-demo-v1",
        t=3.0,
        event_type="collision",
        severity="critical",
    )
    with pytest.raises(ValidationError, match="episode duration"):
        _episode(events=[event])


def test_episode_event_dataset_version_is_checked() -> None:
    event = EpisodeEvent(
        event_id="event-1",
        dataset_version_id="other-version",
        t=1.0,
        event_type="collision",
        severity="critical",
    )
    with pytest.raises(ValidationError, match="dataset_version_id"):
        _episode(events=[event])


def test_failure_type_values_are_stable() -> None:
    assert {item.value for item in FailureType} >= {
        "grasp_miss",
        "occlusion",
        "collision",
        "planner_unreachable",
        "timeout",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("joint_positions", [float("inf")] * 6),
        ("end_effector_pose", [float("-inf")] + [0.0] * 6),
        ("gripper_width", float("inf")),
        ("gripper_force", float("nan")),
    ],
)
def test_observation_rejects_non_finite_sensor_values(field: str, value: object) -> None:
    payload = _observation(0.0).model_dump()
    payload[field] = value
    with pytest.raises(ValidationError):
        EpisodeObservation.model_validate(payload)
