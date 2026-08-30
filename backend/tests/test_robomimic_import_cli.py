import json
from pathlib import Path

import h5py
import numpy as np
import pytest

from signalforge.db.connection import Database
from signalforge.db.repositories import get_dataset_version, get_embodied_episode
from signalforge.embodied.robomimic_import import build_robomimic_manifest
from signalforge.embodied.robomimic_ingest import RobomimicIngestError, import_robomimic_artifact


def _artifact(tmp_path: Path) -> Path:
    path = tmp_path / "low_dim.hdf5"
    with h5py.File(path, "w") as handle:
        group = handle.create_group("data").create_group("demo_0")
        group.create_dataset("actions", data=np.zeros((2, 7), dtype=np.float32))
        group.create_dataset("states", data=np.zeros((2, 14), dtype=np.float32))
        group.create_dataset("success", data=np.array([False, True], dtype=np.bool_))
    path.with_suffix(".json").write_text(
        json.dumps({"episodes": [{"episode_id": 0, "success": True}]}), encoding="utf-8"
    )
    return path


def test_import_is_idempotent_and_preserves_public_simulation_provenance(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    manifest = build_robomimic_manifest(artifact, dataset_version_id="robomimic-lift-v1")
    db = Database(tmp_path / "robomimic.duckdb")
    db.apply_schema()

    first = import_robomimic_artifact(db, artifact=artifact, manifest=manifest)
    second = import_robomimic_artifact(db, artifact=artifact, manifest=manifest)

    version = get_dataset_version(db, "robomimic-lift-v1")
    assert first.imported_count == 1
    assert second.imported_count == 0
    assert version is not None
    assert version.source_name == manifest.source_name
    assert str(version.source_url) == manifest.source_url
    assert get_embodied_episode(db, "robomimic-lift-v1", "robomimic-lift-0") is not None


def test_import_rejects_same_version_with_different_artifact_hash(tmp_path: Path) -> None:
    artifact = _artifact(tmp_path)
    db = Database(tmp_path / "robomimic.duckdb")
    db.apply_schema()
    original = build_robomimic_manifest(artifact, dataset_version_id="robomimic-lift-v1")
    import_robomimic_artifact(db, artifact=artifact, manifest=original)

    changed = original.__class__(**{**original.__dict__, "sha256": "0" * 64})
    with pytest.raises(RobomimicIngestError, match="ROBOMIMIC_DATASET_VERSION_CONFLICT"):
        import_robomimic_artifact(db, artifact=artifact, manifest=changed)
