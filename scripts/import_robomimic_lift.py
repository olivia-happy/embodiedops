"""Import a locally acquired Robomimic Lift HDF5 artifact into DuckDB."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import h5py

from signalforge.db.connection import Database
from signalforge.embodied.robomimic_import import RobomimicImportManifest
from signalforge.embodied.robomimic_ingest import import_robomimic_artifact


def _manifest(path: Path) -> RobomimicImportManifest:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return RobomimicImportManifest(
        dataset_version_id=str(payload["dataset_version_id"]),
        source_name=str(payload["source_name"]),
        source_url=str(payload["source_url"]),
        sha256=str(payload["sha256"]),
        data_type=str(payload["data_type"]),
        real_robot_data=bool(payload["real_robot_data"]),
        task_id=str(payload["task_id"]),
        episode_count=int(payload.get("episode_count", 0)),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--artifact", type=Path, default=Path("data/embodied/raw/robomimic/low_dim.hdf5")
    )
    parser.add_argument(
        "--manifest", type=Path, default=Path("data/embodied/raw/robomimic/manifest.json")
    )
    args = parser.parse_args()
    db = Database(args.database)
    db.apply_schema()
    manifest = _manifest(args.manifest)
    if manifest.episode_count == 0:
        with h5py.File(args.artifact, "r") as source:
            data = source.get("data")
            if not isinstance(data, h5py.Group) or not data:
                raise ValueError("ROBOMIMIC_EMPTY_DATASET")
            manifest = RobomimicImportManifest(
                **{**manifest.__dict__, "episode_count": len(data)}
            )
        payload = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
        payload["episode_count"] = manifest.episode_count
        args.manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result = import_robomimic_artifact(
        db, artifact=args.artifact, manifest=_manifest(args.manifest)
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
