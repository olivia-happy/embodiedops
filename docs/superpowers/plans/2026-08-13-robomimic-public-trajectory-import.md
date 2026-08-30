# Robomimic Public Trajectory Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import an approved small public simulation trajectory file into the versioned EmbodiedOps analysis path without misrepresenting it as real-robot evidence.

**Architecture:** A local acquisition script downloads the published Robomimic Lift low-dimensional HDF5 artifact and writes a source manifest. A Python adapter converts deterministic trajectory state and terminal outcome fields into the existing strict `Episode` contract. A local CLI, not an HTTP endpoint, transactionally inserts the new immutable version into DuckDB.

**Tech Stack:** Python 3.11, h5py, Pydantic, DuckDB, Pytest, PowerShell.

## Global Constraints

- Source is public simulation data only; `real_robot_data` is always false.
- Keep the original HDF5 outside the database and out of Git tracking.
- No model call, cloud inference, physical robot control, or paid service.
- All source URLs, hashes, import timestamps, and dataset versions are explicit.
- Unit tests construct tiny HDF5 fixtures locally and never download a dataset.

---

### Task 1: Source artifact acquisition and provenance

**Files:**
- Create: `scripts/acquire_robomimic_lift.ps1`
- Modify: `.gitignore`
- Create: `backend/tests/test_acquire_robomimic_script.py`

**Interfaces:**
- Produces: `data/embodied/raw/robomimic_lift_ph_low_dim.hdf5` and adjacent JSON manifest.
- Manifest fields: `dataset_version_id`, `source_name`, `source_url`, `sha256`, `data_type`, `real_robot_data`, `task_id`.

- [ ] **Step 1: Write failing script contract tests**

```python
def test_acquisition_script_uses_https_and_writes_manifest():
    script = Path("scripts/acquire_robomimic_lift.ps1").read_text(encoding="utf-8")
    assert "https://downloads.cs.stanford.edu" in script
    assert "Get-FileHash" in script
    assert "real_robot_data" in script
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest backend\tests\test_acquire_robomimic_script.py -q`
Expected: FAIL because the script does not exist.

- [ ] **Step 3: Implement the acquisition script and ignores**

The script accepts an optional output root, downloads only HTTPS from the official Stanford host, avoids re-download when the artifact and manifest hash agree, computes SHA-256, and emits a manifest with public simulation provenance. Add `data/embodied/raw/` to `.gitignore`.

- [ ] **Step 4: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest backend\tests\test_acquire_robomimic_script.py -q`
Expected: PASS.

### Task 2: Deterministic HDF5 adapter

**Files:**
- Create: `backend/signalforge/embodied/robomimic_import.py`
- Create: `backend/tests/test_robomimic_import.py`

**Interfaces:**
- Consumes: `Path` to HDF5 and metadata JSON plus immutable `dataset_version_id`.
- Produces: `RobomimicImportManifest`, `list[Episode]`, and stable `RobomimicImportError(code)`.

- [ ] **Step 1: Write failing adapter tests using a tiny local HDF5**

```python
def test_adapter_maps_terminal_success_and_failure(tmp_path: Path):
    path = write_hdf5_fixture(tmp_path, success=[False, True])
    episodes = load_robomimic_episodes(path, dataset_version_id="robomimic-lift-v1")
    assert episodes[0].outcome.success is True
    assert episodes[0].dataset_version_id == "robomimic-lift-v1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest backend\tests\test_robomimic_import.py -q`
Expected: FAIL because the adapter does not exist.

- [ ] **Step 3: Implement strict, deterministic conversion**

Read only supported HDF5 keys; sample state records into six joint values and seven pose values deterministically; map a terminal `success=true` to success and only deterministic terminal timeout/failure flags to a known failure type. Reject unsupported shapes or absent terminal outcome with stable error codes. Do not infer model diagnoses or add fabricated events.

- [ ] **Step 4: Run adapter and contract tests**

Run: `.venv\Scripts\python.exe -m pytest backend\tests\test_robomimic_import.py backend\tests\test_embodied_episode_models.py -q`
Expected: PASS.

### Task 3: Transactional local import and public source card

**Files:**
- Create: `scripts/import_robomimic_lift.py`
- Create: `backend/tests/test_robomimic_import_cli.py`
- Create: `docs/EMBODIEDOPS_ROBOMIMIC_SOURCE_CARD.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: acquired artifact plus its manifest.
- Produces: one immutable DuckDB dataset version and normalized embodied episodes.

- [ ] **Step 1: Write failing CLI integration tests**

```python
def test_import_is_idempotent_and_preserves_simulation_provenance(tmp_path: Path):
    result = import_robomimic_artifact(db_path, artifact, manifest)
    assert result.imported_count == 1
    assert result.provenance["real_robot_data"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest backend\tests\test_robomimic_import_cli.py -q`
Expected: FAIL because the local importer does not exist.

- [ ] **Step 3: Implement the local importer**

Validate source-host/provenance/hash/version before opening a transaction. Use `insert_dataset_version` and `insert_embodied_episodes`; rollback on any error. A repeated exact import returns idempotent; a matching version with a differing file hash fails closed.

- [ ] **Step 4: Document reuse and SQL boundary**

Document upstream source, simulation scope, expected HDF5 keys, immutable artifact storage, DuckDB tables/query role, and the future ROS/controller-log adapter contract.

- [ ] **Step 5: Run verification**

Run: `.venv\Scripts\python.exe -m pytest backend\tests\test_robomimic_import_cli.py backend\tests\test_embodied_repositories.py -q`
Expected: PASS.

### Task 4: Acquire approved data and end-to-end verify

**Files:**
- Create locally, ignored: `data/embodied/raw/robomimic_lift_ph_low_dim.hdf5`
- Create locally, ignored: `data/embodied/raw/robomimic_lift_ph_low_dim.manifest.json`

- [ ] **Step 1: Run acquisition**

Run: `powershell -ExecutionPolicy Bypass -File scripts\acquire_robomimic_lift.ps1`
Expected: an HTTPS download from the official source, a SHA-256 manifest, and no data sent anywhere.

- [ ] **Step 2: Run importer to a fresh local DuckDB**

Run: `.venv\Scripts\python.exe scripts\import_robomimic_lift.py --database data\robomimic-demo.duckdb`
Expected: a single simulation dataset version and an explicit episode count.

- [ ] **Step 3: Run test and static checks**

Run: `.venv\Scripts\python.exe -m pytest backend\tests\test_robomimic_import.py backend\tests\test_robomimic_import_cli.py -q`
Expected: PASS.

Run: `.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge\embodied backend\tests scripts`
Expected: `All checks passed!`.
