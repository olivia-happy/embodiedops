# EmbodiedOps Robomimic source card

## What this source is

The first external source is Robomimic v0.1, Lift, Proficient-Human,
low-dimensional trajectories. It is publicly released simulation data, not
data collected from a physical robot in this project. The source artifact is
an HDF5 trajectory file; its adjacent JSON contains environment and episode
metadata. See the [official Robomimic data documentation](https://robomimic.github.io/docs/v0.2/datasets/robomimic_v0.1.html).

## Storage and SQL boundary

`data/embodied/raw/robomimic/low_dim.hdf5` is an immutable local source
artifact and is ignored by Git. The acquisition manifest stores the official
URL and SHA-256. DuckDB holds only normalized, version-scoped episodes and
derived metrics. It is used for analysis and UI queries, not as a raw-video,
ROS bag, or high-frequency sensor archive.

## Import and reuse

Run `scripts/acquire_robomimic_lift.ps1`, then run
`scripts/import_robomimic_lift.py --database data/robomimic-demo.duckdb`.
The adapter accepts only the approved Stanford HTTPS source and writes
`public_simulation`, `real_robot_data=false` provenance. It is idempotent for
the same hash and rejects a changed artifact under an existing dataset version.

The acquired Lift Proficient-Human artifact currently contains 200 successful
simulation trajectories. It is useful as an external import/replay baseline,
but it is not a failure-analysis benchmark. EmbodiedOps retains its separate
synthetic failure fixture for diagnosis regression; a future permissioned
real-world failure cohort must be imported as a separate immutable version.

Future ROS bag, controller CSV, or permissioned company export needs a separate
adapter that outputs the same `Episode` contract and passes the real-data card
and annotation protocol. It cannot be merged into this public simulation
dataset version or used to claim sim-to-real performance.
