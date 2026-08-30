# Robomimic Public Trajectory Import Design

## Goal

Add one small, publicly available simulation dataset to EmbodiedOps so the
system demonstrates a real external-data ingestion path without representing
simulation trajectories as real-robot evidence.

## Scope

The first source is the `Lift` Proficient-Human low-dimensional Robomimic v0.1
dataset. Its published size is approximately 18 MB. The project downloads the
HDF5 artifact only after user approval, retains the original file outside the
application database, and records source URL, SHA-256, license/citation note,
task name, and import time in a manifest.

The importer reads the HDF5 trajectories and their metadata. It creates a new
immutable dataset version whose provenance explicitly says `simulation`,
`public`, and `real_robot_data=false`. For each trajectory it writes the
existing EmbodiedOps `Episode`, sampled observations, bounded events derived
only from deterministic terminal flags, and an outcome. No model-generated
events, labels, camera frames, or hidden claims are manufactured during import.

## Storage Boundary

The HDF5 file remains an immutable source artifact under `data/embodied/raw/`
and is excluded from public Git tracking when its size or license requires it.
DuckDB stores normalized records only: dataset version, episode, observation,
event, outcome, import manifest metadata, and derived metrics. SQL is therefore
used for version-scoped joins, failure distributions, phase analysis, and UI
queries; it is not used as a replacement for HDF5, video, ROS bags, or raw
sensor archives.

## Reuse Contract

The import boundary exposes a source-specific `RobomimicImportRequest` and a
source-neutral EmbodiedOps episode output. A future ROS bag, controller CSV,
or permissioned corporate export must use its own adapter and pass the existing
`RealRobotDataCard` and annotation gates. It cannot inherit the public
simulation provenance or be merged into the same dataset version.

## Failure Handling

The download and import fail closed on a missing file, invalid HDF5 structure,
hash mismatch, metadata/trajectory inconsistency, duplicated conflicting
dataset version, or a source marked as real robot data without a real-data card.
Every failure returns a stable code and does not partially persist a version.

## Verification

Unit tests use a tiny synthetic HDF5 fixture and never download data. An
opt-in PowerShell acquisition script downloads the approved public source,
computes its hash, produces a manifest, and invokes the importer. Integration
tests prove idempotent import, version isolation, correct simulation provenance,
and aggregate success/failure metrics. The UI must label the source as public
simulation and link to the source card.

## Non-Goals

This change does not train or deploy a policy, control a robot, claim
sim-to-real transfer, claim a real-world ROI, or download any commercial,
private, or company-internal data.
