from pathlib import Path


def test_acquisition_script_uses_official_https_source_and_hash_manifest() -> None:
    script = Path("scripts/acquire_robomimic_lift.ps1").read_text(encoding="utf-8")

    assert "https://downloads.cs.stanford.edu/downloads/rt_benchmark/lift/ph/low_dim.hdf5" in script
    assert "Get-FileHash" in script
    assert "real_robot_data = $false" in script
    assert "episode_count" in script
    assert "robomimic-lift-ph-low-dim-v1" in script


def test_raw_trajectory_directory_is_ignored() -> None:
    ignored = Path(".gitignore").read_text(encoding="utf-8")

    assert "data/embodied/raw/" in ignored
