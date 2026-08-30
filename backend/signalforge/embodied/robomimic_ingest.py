"""Transactional local ingestion for an approved Robomimic source artifact."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from signalforge.db.connection import Database
from signalforge.db.repositories import (
    get_dataset_version,
    insert_dataset_version,
    insert_embodied_episodes,
)
from signalforge.embodied.robomimic_import import (
    RobomimicImportManifest,
    _sha256,
    load_robomimic_episodes,
)


class RobomimicIngestError(ValueError):
    """Stable failure for local transactional import."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RobomimicIngestResult:
    dataset_version_id: str
    imported_count: int
    idempotent: bool
    provenance: dict[str, object]


def import_robomimic_artifact(
    db: Database,
    *,
    artifact: Path,
    manifest: RobomimicImportManifest,
) -> RobomimicIngestResult:
    """Insert one public simulation version without exposing a remote importer."""

    if manifest.real_robot_data or manifest.data_type != "public_simulation":
        raise RobomimicIngestError("ROBOMIMIC_PROVENANCE_INVALID")
    if not manifest.source_url.startswith("https://downloads.cs.stanford.edu/"):
        raise RobomimicIngestError("ROBOMIMIC_SOURCE_UNAPPROVED")
    existing = get_dataset_version(db, manifest.dataset_version_id)
    if existing is not None and existing.file_hash != manifest.sha256:
        raise RobomimicIngestError("ROBOMIMIC_DATASET_VERSION_CONFLICT")
    if not artifact.is_file():
        raise RobomimicIngestError("ROBOMIMIC_ARTIFACT_MISSING")
    if _sha256(artifact) != manifest.sha256:
        raise RobomimicIngestError("ROBOMIMIC_ARTIFACT_HASH_MISMATCH")

    episodes = load_robomimic_episodes(
        artifact, dataset_version_id=manifest.dataset_version_id
    )
    if len(episodes) != manifest.episode_count:
        raise RobomimicIngestError("ROBOMIMIC_MANIFEST_COUNT_MISMATCH")

    try:
        if existing is None:
            insert_dataset_version(
                db,
                manifest.dataset_version_id,
                manifest.source_name,
                manifest.sha256,
                manifest.episode_count,
                source_url=manifest.source_url,
            )
        inserted = insert_embodied_episodes(db, episodes)
    except Exception:
        raise RobomimicIngestError("ROBOMIMIC_PERSISTENCE_FAILED") from None
    return RobomimicIngestResult(
        dataset_version_id=manifest.dataset_version_id,
        imported_count=inserted,
        idempotent=inserted == 0,
        provenance={
            "data_type": manifest.data_type,
            "real_robot_data": manifest.real_robot_data,
            "source_url": manifest.source_url,
        },
    )
