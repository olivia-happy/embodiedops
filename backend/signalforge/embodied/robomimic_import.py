"""Local-only adapter for public Robomimic simulation trajectories.

This module reads a downloaded HDF5 source artifact. It does not download
data, train a policy, send data to a model, or communicate with a robot.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from signalforge.embodied.models import Episode, EpisodeObservation, EpisodeOutcome

ROBOMIMIC_LIFT_URL = "https://downloads.cs.stanford.edu/downloads/rt_benchmark/lift/ph/low_dim.hdf5"
ROBOMIMIC_LIFT_VERSION = "robomimic-lift-ph-low-dim-v1"


class RobomimicImportError(ValueError):
    """Stable import failure that never includes source trajectory contents."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RobomimicImportManifest:
    dataset_version_id: str
    source_name: str
    source_url: str
    sha256: str
    data_type: str
    real_robot_data: bool
    task_id: str
    episode_count: int

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_version_id": self.dataset_version_id,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "sha256": self.sha256,
            "data_type": self.data_type,
            "real_robot_data": self.real_robot_data,
            "task_id": self.task_id,
            "episode_count": self.episode_count,
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_metadata(path: Path) -> dict[str, Any]:
    metadata_path = path.with_suffix(".json")
    if not metadata_path.is_file():
        return {}
    try:
        parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RobomimicImportError("ROBOMIMIC_METADATA_INVALID") from exc
    if not isinstance(parsed, dict):
        raise RobomimicImportError("ROBOMIMIC_METADATA_INVALID")
    return parsed


def _dataset(group: h5py.Group, *paths: str) -> np.ndarray | None:
    for path in paths:
        item = group.get(path)
        if isinstance(item, h5py.Dataset):
            return np.asarray(item)
    return None


def _series_at(values: np.ndarray | None, index: int, width: int) -> list[float]:
    if values is None or values.ndim < 2 or index >= values.shape[0]:
        return [0.0] * width
    vector = np.asarray(values[index]).reshape(-1).astype(float)
    result = vector[:width].tolist()
    return result + [0.0] * (width - len(result))


def _terminal_success(group: h5py.Group, metadata_episode: dict[str, Any] | None) -> bool:
    success = _dataset(group, "success")
    if success is not None and success.size:
        return bool(np.asarray(success).reshape(-1)[-1])
    if metadata_episode and isinstance(metadata_episode.get("success"), bool):
        return bool(metadata_episode["success"])
    rewards = _dataset(group, "rewards")
    if rewards is not None and rewards.size:
        terminal_reward = float(np.asarray(rewards).reshape(-1)[-1])
        if terminal_reward == 1.0:
            return True
    raise RobomimicImportError("ROBOMIMIC_TERMINAL_OUTCOME_MISSING")


def _metadata_episodes(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = metadata.get("episodes", [])
    if not isinstance(entries, list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if isinstance(entry, dict) and "episode_id" in entry:
            result[str(entry["episode_id"])] = entry
    return result


def _episode_from_group(
    group: h5py.Group,
    *,
    group_name: str,
    dataset_version_id: str,
    metadata_episode: dict[str, Any] | None,
) -> Episode:
    actions = _dataset(group, "actions")
    states = _dataset(group, "states")
    joints = _dataset(group, "obs/robot0_joint_pos")
    if joints is None:
        joints = states
    position = _dataset(group, "obs/robot0_eef_pos")
    quaternion = _dataset(group, "obs/robot0_eef_quat")
    gripper = _dataset(group, "obs/robot0_gripper_qpos")
    if actions is None or actions.ndim != 2:
        raise RobomimicImportError("ROBOMIMIC_ACTIONS_INVALID")
    steps = int(actions.shape[0])
    if steps < 1:
        raise RobomimicImportError("ROBOMIMIC_EMPTY_TRAJECTORY")
    if states is not None and (states.ndim != 2 or states.shape[0] not in {steps, steps + 1}):
        raise RobomimicImportError("ROBOMIMIC_STATES_INVALID")

    observations: list[EpisodeObservation] = []
    for index in range(steps):
        xyz = _series_at(position, index, 3)
        quat = _series_at(quaternion, index, 4)
        if position is None or quaternion is None:
            fallback = _series_at(states, index, 13)
            xyz, quat = fallback[6:9], fallback[9:13]
        gripper_values = _series_at(gripper, index, 1)
        observations.append(
            EpisodeObservation(
                dataset_version_id=dataset_version_id,
                t=float(index),
                joint_positions=_series_at(joints, index, 6),
                end_effector_pose=[*xyz, *quat],
                gripper_width=abs(float(gripper_values[0])),
                gripper_force=None,
                camera_frame_id=f"{group_name}-state-{index:05d}",
            )
        )

    episode_id = str(metadata_episode.get("episode_id")) if metadata_episode else group_name
    return Episode(
        episode_id=f"robomimic-lift-{episode_id}",
        dataset_version_id=dataset_version_id,
        task_id="robomimic_lift",
        scene_id="robomimic_lift_simulation",
        seed=int(metadata_episode.get("seed", 0)) if metadata_episode else 0,
        instruction="Lift the object in the Robomimic simulation task.",
        robot_model="arm6_gripper",
        observations=observations,
        events=[],
        outcome=EpisodeOutcome(
            success=_terminal_success(group, metadata_episode),
            failure_type=None,
            completion_time_s=float(steps - 1),
        ),
    )


def load_robomimic_episodes(path: Path, *, dataset_version_id: str) -> list[Episode]:
    """Convert an approved local Lift HDF5 artifact without network access."""

    if not path.is_file():
        raise RobomimicImportError("ROBOMIMIC_ARTIFACT_MISSING")
    metadata = _read_metadata(path)
    known_metadata = _metadata_episodes(metadata)
    try:
        with h5py.File(path, "r") as handle:
            data = handle.get("data")
            if not isinstance(data, h5py.Group):
                raise RobomimicImportError("ROBOMIMIC_DATA_GROUP_MISSING")
            groups = [(name, item) for name, item in data.items() if isinstance(item, h5py.Group)]
            if not groups:
                raise RobomimicImportError("ROBOMIMIC_EMPTY_DATASET")
            return [
                _episode_from_group(
                    group,
                    group_name=name,
                    dataset_version_id=dataset_version_id,
                    metadata_episode=known_metadata.get(name.removeprefix("demo_")),
                )
                for name, group in sorted(groups)
            ]
    except OSError as exc:
        raise RobomimicImportError("ROBOMIMIC_HDF5_INVALID") from exc


def build_robomimic_manifest(path: Path, *, dataset_version_id: str) -> RobomimicImportManifest:
    """Return public simulation provenance; original trajectory bytes stay local."""

    episodes = load_robomimic_episodes(path, dataset_version_id=dataset_version_id)
    return RobomimicImportManifest(
        dataset_version_id=dataset_version_id,
        source_name="robomimic_v0.1_lift_proficient_human_low_dim",
        source_url=ROBOMIMIC_LIFT_URL,
        sha256=_sha256(path),
        data_type="public_simulation",
        real_robot_data=False,
        task_id="robomimic_lift",
        episode_count=len(episodes),
    )
