from __future__ import annotations

import json
from pathlib import Path

from signalforge.embodied.simulator import (
    build_demo_manifest,
    generate_demo_episodes,
    write_demo_fixture,
)


def test_demo_generator_is_deterministic_and_covers_failure_modes() -> None:
    first = generate_demo_episodes(count=12, seed=123, dataset_version_id="embodied-demo-v1")
    second = generate_demo_episodes(count=12, seed=123, dataset_version_id="embodied-demo-v1")

    assert [item.model_dump(mode="json") for item in first] == [
        item.model_dump(mode="json") for item in second
    ]
    failure_types = {item.outcome.failure_type.value for item in first if item.outcome.failure_type}
    assert failure_types >= {
        "grasp_miss",
        "occlusion",
        "collision",
        "planner_unreachable",
        "timeout",
    }
    assert all(item.dataset_version_id == "embodied-demo-v1" for item in first)
    assert all(item.robot_model == "arm6_gripper" for item in first)


def test_demo_generator_rejects_invalid_arguments() -> None:
    try:
        generate_demo_episodes(count=0, seed=1, dataset_version_id="v1")
    except ValueError as exc:
        assert "count" in str(exc)
    else:
        raise AssertionError("count=0 should be rejected")


def test_fixture_writer_returns_manifest_with_hash(tmp_path: Path) -> None:
    fixture_path = tmp_path / "episodes.jsonl"
    manifest_path = tmp_path / "manifest.json"
    episodes = generate_demo_episodes(count=6, seed=9, dataset_version_id="embodied-demo-v1")
    manifest = write_demo_fixture(
        episodes, fixture_path=fixture_path, manifest_path=manifest_path
    )

    assert fixture_path.exists()
    assert manifest_path.exists()
    assert manifest["count"] == 6
    assert manifest["schema_version"] == "embodied.episode.v1"
    assert len(manifest["sha256"]) == 64
    assert manifest["failure_distribution"]
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_manifest_builder_is_pure() -> None:
    episodes = generate_demo_episodes(count=3, seed=4, dataset_version_id="embodied-demo-v2")
    manifest = build_demo_manifest(episodes, sha256="0" * 64)
    assert manifest["dataset_version_id"] == "embodied-demo-v2"
    assert manifest["seed"] == 4
    assert manifest["sha256"] == "0" * 64
