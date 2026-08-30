import json
from pathlib import Path

import pytest

from signalforge.embodied.robomimic_import import (
    RobomimicImportError,
    build_robomimic_manifest,
    load_robomimic_episodes,
)


def _write_fixture(path: Path, *, terminal_success: bool | None) -> None:
    import h5py
    import numpy as np

    with h5py.File(path, "w") as handle:
        trajectory = handle.create_group("data").create_group("demo_0")
        trajectory.create_dataset("actions", data=np.zeros((3, 7), dtype=np.float32))
        trajectory.create_dataset("states", data=np.array([[0.0] * 14, [0.1] * 14, [0.2] * 14]))
        if terminal_success is not None:
            trajectory.create_dataset(
                "success", data=np.array([False, False, terminal_success], dtype=np.bool_)
            )
    episode_metadata = {"episode_id": 0}
    if terminal_success is not None:
        episode_metadata["success"] = terminal_success
    metadata = {
        "env_info": {"env_name": "Lift"},
        "episodes": [episode_metadata],
    }
    path.with_suffix(".json").write_text(json.dumps(metadata), encoding="utf-8")


def test_adapter_maps_trajectory_to_versioned_simulation_episode(tmp_path: Path) -> None:
    artifact = tmp_path / "lift.hdf5"
    _write_fixture(artifact, terminal_success=True)

    episodes = load_robomimic_episodes(artifact, dataset_version_id="robomimic-lift-v1")

    assert len(episodes) == 1
    assert episodes[0].dataset_version_id == "robomimic-lift-v1"
    assert episodes[0].outcome.success is True
    assert episodes[0].outcome.failure_type is None
    assert episodes[0].robot_model == "arm6_gripper"
    assert len(episodes[0].observations) == 3


def test_adapter_rejects_absent_terminal_outcome(tmp_path: Path) -> None:
    artifact = tmp_path / "lift.hdf5"
    _write_fixture(artifact, terminal_success=None)

    with pytest.raises(RobomimicImportError, match="ROBOMIMIC_TERMINAL_OUTCOME_MISSING"):
        load_robomimic_episodes(artifact, dataset_version_id="robomimic-lift-v1")


def test_manifest_is_explicitly_public_simulation(tmp_path: Path) -> None:
    artifact = tmp_path / "lift.hdf5"
    _write_fixture(artifact, terminal_success=True)

    manifest = build_robomimic_manifest(artifact, dataset_version_id="robomimic-lift-v1")

    assert manifest.real_robot_data is False
    assert manifest.data_type == "public_simulation"
    assert manifest.source_url.startswith("https://downloads.cs.stanford.edu/")
