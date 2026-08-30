"""Generate the reproducible EmbodiedOps synthetic episode fixture."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from signalforge.embodied.simulator import generate_demo_episodes, write_demo_fixture  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--dataset-version-id", default="embodied-demo-v1")
    parser.add_argument(
        "--fixture-path",
        type=Path,
        default=PROJECT_ROOT / "data" / "embodied" / "demo_episodes.jsonl",
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=PROJECT_ROOT / "data" / "embodied" / "demo_episode_manifest.json",
    )
    args = parser.parse_args()
    episodes = generate_demo_episodes(
        count=args.count, seed=args.seed, dataset_version_id=args.dataset_version_id
    )
    manifest = write_demo_fixture(
        episodes, fixture_path=args.fixture_path, manifest_path=args.manifest_path
    )
    print(
        f"Generated {manifest['count']} episodes at {args.fixture_path} "
        f"(dataset_version_id={manifest['dataset_version_id']}, sha256={manifest['sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
