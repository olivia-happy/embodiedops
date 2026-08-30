"""Deterministic tabletop pick-and-place episode fixtures.

This module is intentionally a small simulator-compatible fixture generator,
not a physics engine.  It produces realistic-shaped records with explicit
failure injection so the downstream diagnosis and evaluation code can be
tested without network access, a cloud model, or a physical robot.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from .models import Episode, EpisodeEvent, EpisodeObservation, EpisodeOutcome, FailureType

SCHEMA_VERSION = "embodied.episode.v1"
_FAILURE_SEQUENCE: tuple[FailureType | None, ...] = (
    FailureType.GRASP_MISS,
    FailureType.OCCLUSION,
    FailureType.COLLISION,
    FailureType.PLANNER_UNREACHABLE,
    FailureType.TIMEOUT,
    None,
)


def _event_for_failure(
    failure_type: FailureType | None, *, dataset_version_id: str, episode_id: str
) -> EpisodeEvent | None:
    if failure_type is None:
        return None
    event_by_failure = {
        FailureType.GRASP_MISS: (2.0, "grasp_contact", "warning"),
        FailureType.OCCLUSION: (1.5, "occlusion", "warning"),
        FailureType.COLLISION: (2.5, "collision", "critical"),
        FailureType.PLANNER_UNREACHABLE: (1.0, "planner_error", "critical"),
        FailureType.TIMEOUT: (4.0, "timeout", "critical"),
    }
    t, event_type, severity = event_by_failure[failure_type]
    return EpisodeEvent(
        dataset_version_id=dataset_version_id,
        event_id=f"{episode_id}-event-001",
        t=t,
        event_type=event_type,
        severity=severity,
    )


def generate_demo_episodes(
    *, count: int, seed: int, dataset_version_id: str
) -> list[Episode]:
    """Generate reproducible ``arm6_gripper`` tabletop episodes.

    Failure modes rotate through grasp miss, occlusion, collision, planner
    unreachable and timeout, with every sixth episode being a successful
    control case.  The seed only controls bounded sensor jitter; it never
    changes the failure label sequence.
    """

    if count < 1:
        raise ValueError("count must be at least 1")
    if not dataset_version_id.strip():
        raise ValueError("dataset_version_id must not be empty")

    rng = random.Random(seed)
    episodes: list[Episode] = []
    for index in range(count):
        episode_id = f"embodied-demo-{index + 1:04d}"
        failure_type = _FAILURE_SEQUENCE[index % len(_FAILURE_SEQUENCE)]
        observations: list[EpisodeObservation] = []
        for step in range(5):
            t = float(step)
            jitter = rng.uniform(-0.0025, 0.0025)
            observations.append(
                EpisodeObservation(
                    dataset_version_id=dataset_version_id,
                    t=t,
                    joint_positions=[
                        round(0.02 * step + jitter + joint * 0.001, 6) for joint in range(6)
                    ],
                    end_effector_pose=[
                        round(0.42 + 0.015 * step + jitter, 6),
                        round(0.05 + 0.002 * index, 6),
                        round(0.12 + 0.01 * step, 6),
                        0.0,
                        0.0,
                        0.0,
                        1.0,
                    ],
                    gripper_width=round(max(0.005, 0.045 - 0.008 * step), 6),
                    gripper_force=round(4.0 + 0.5 * step, 6),
                    camera_frame_id=f"{episode_id}-frame-{step:03d}",
                )
            )
        event = _event_for_failure(
            failure_type, dataset_version_id=dataset_version_id, episode_id=episode_id
        )
        episodes.append(
            Episode(
                episode_id=episode_id,
                dataset_version_id=dataset_version_id,
                task_id="tabletop_pick_place",
                scene_id=f"tabletop-scene-{index % 3 + 1:02d}",
                seed=seed + index,
                instruction="Pick the red cube and place it in the target box.",
                robot_model="arm6_gripper",
                observations=observations,
                events=[event] if event is not None else [],
                outcome=EpisodeOutcome(
                    success=failure_type is None,
                    failure_type=failure_type,
                    completion_time_s=4.0,
                ),
            )
        )
    return episodes


def _jsonl_bytes(episodes: Iterable[Episode]) -> bytes:
    lines = [
        json.dumps(
            episode.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        for episode in episodes
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_demo_manifest(episodes: Iterable[Episode], *, sha256: str) -> dict[str, object]:
    """Build a machine-readable manifest without touching the filesystem."""

    episode_list = list(episodes)
    if not episode_list:
        raise ValueError("episodes must not be empty")
    versions = {episode.dataset_version_id for episode in episode_list}
    if len(versions) != 1:
        raise ValueError("all episodes must share one dataset_version_id")
    failure_distribution = Counter(
        episode.outcome.failure_type.value if episode.outcome.failure_type else "success"
        for episode in episode_list
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_version_id": episode_list[0].dataset_version_id,
        "seed": episode_list[0].seed,
        "count": len(episode_list),
        "sha256": sha256,
        "failure_distribution": dict(sorted(failure_distribution.items())),
        "source": "synthetic_tabletop_fixture",
        "real_robot_data": False,
    }


def write_demo_fixture(
    episodes: Iterable[Episode], *, fixture_path: Path, manifest_path: Path
) -> dict[str, object]:
    """Write deterministic JSONL episodes and a provenance manifest."""

    episode_list = list(episodes)
    payload = _jsonl_bytes(episode_list)
    digest = hashlib.sha256(payload).hexdigest()
    manifest = build_demo_manifest(episode_list, sha256=digest)
    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_path.write_bytes(payload)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
