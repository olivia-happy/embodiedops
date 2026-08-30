# EmbodiedOps Delivery and Business Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** Make EmbodiedOps reliably start as a read-only demo, expose an honest robotics product data/evaluation contract, and present a credible business and interview-ready evidence chain.

**Architecture:** Keep deterministic episode parsing, phase analysis, metrics, and safety validation in the backend. Add a hash-gated demo bootstrap during read-only app startup, a versioned annotation/evaluation layer for approved real-robot exports, and a frontend evidence-chain view that never sends robot commands. Keep Ollama local-only and treat synthetic fixtures as regression data, not production proof.

**Tech Stack:** FastAPI, DuckDB, Pydantic, Next.js 15, TypeScript, Vitest, pytest, Ruff, Docker Compose, PowerShell.

## Global Constraints

- No cloud model, paid API, API key, public database, or real robot control.
- Read-only demo requires both `DEMO_READ_ONLY=true` and `NEXT_PUBLIC_DEMO_READ_ONLY=true`.
- Demo bootstrap only reads `data/embodied/demo_episodes.jsonl` and `demo_episode_manifest.json`, validates SHA-256, and is idempotent.
- Dataset versions are immutable; missing/invalid/conflicting data fails closed.
- Synthetic fixtures must be labelled `real_robot_data=false`, `live_model=false`, and `manual_review_status=not_performed`.
- Empty denominators render as null/“不足以判断”, never as zero percent.
- Do not persist prompts, raw model responses, API keys, or unredacted sensor payloads in public artifacts.
- All implementation uses TDD: red test, minimal implementation, focused regression, then broader regression.

---

### Task 1: Hash-gated read-only embodied bootstrap

**Files:**
- Modify: `backend/signalforge/api/app.py`
- Test: `backend/tests/test_demo_read_only.py`
- Modify: `README.md`
- Test: `backend/tests/test_demo_integration.py`

**Interfaces:**
- Add `_bootstrap_embodied_demo(database: Database, settings: Settings) -> None`.
- It reads the data directory adjacent to `Settings.database_path`, parses the manifest and JSONL, verifies manifest SHA-256, inserts `dataset_versions` and episodes idempotently, and returns without mutating anything when `demo_read_only` is false.
- App lifespan and injected test databases call the same helper after `apply_schema()`.

- [ ] **Step 1: Write the failing bootstrap tests**

Add a test using a temporary `data/embodied` directory that copies the checked-in fixture and asserts a read-only app can answer `GET /api/v1/embodied/tasks?dataset_version_id=embodied-demo-v1` with `episode_count == 30`. Add tests for missing fixture and hash mismatch returning a deterministic startup error rather than a 200 response with empty metrics.

- [ ] **Step 2: Run the tests to verify the red state**

Run:

```powershell
.venv\Scripts\python.exe -m pytest backend\tests\test_demo_read_only.py -q
```

Expected: the bootstrap test fails because the app only applies schema and does not import the fixture.

- [ ] **Step 3: Implement the minimal bootstrap**

Use `Path(settings.database_path).resolve().parent / "embodied"`, read the manifest and JSONL, compare `sha256(file_bytes)` to `manifest["sha256"]`, call `insert_dataset_version(...)` only when absent, and call `insert_embodied_episodes(...)` for idempotent insertion. Raise a stable `ValueError` for a present-but-mismatched hash. Do not call the diagnosis or model services.

- [ ] **Step 4: Verify focused and integration tests**

Run:

```powershell
.venv\Scripts\python.exe -m pytest backend\tests\test_demo_read_only.py backend\tests\test_demo_integration.py backend\tests\test_embodied_api.py -q
.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge\api\app.py backend\tests\test_demo_read_only.py backend\tests\test_demo_integration.py
```

Expected: all pass, including the existing `DEMO_READ_ONLY` 403 contract.

- [ ] **Step 5: Update the cold-start README path**

Document that the mounted fixture is bootstrapped automatically, that the hash is checked, and that missing fixture files fail closed. Keep the exact PowerShell environment variables in the README.

---

### Task 2: Real-robot export and annotation contracts

**Files:**
- Create: `backend/signalforge/embodied/annotations.py`
- Modify: `backend/signalforge/embodied/models.py`
- Test: `backend/tests/test_embodied_annotations.py`
- Create: `docs/EMBODIEDOPS_REAL_DATA_CARD.md`

**Interfaces:**
- `AnnotationStatus = Literal["unreviewed", "single_review", "double_review", "adjudicated"]`.
- `RootCauseAnnotation` fields: `annotation_id`, `dataset_version_id`, `episode_id`, `failure_phase`, `root_cause`, `supporting_event_ids`, `counter_event_ids`, `confidence`, `annotator_id`, `review_status`, `reviewer_id | None`, `notes | None`.
- `RealRobotDataCard` fields: `dataset_version_id`, `source_name`, `real_robot_data`, `robot_model`, `firmware`, `task_family`, `license_or_permission`, `redaction_status`, `holdout_policy`.
- `validate_annotation(annotation, episode) -> tuple[bool, str | None]` rejects cross-version IDs, unknown events, invalid confidence, and `adjudicated` without reviewer.

- [ ] **Step 1: Write failing contract tests**

Cover valid double-review annotations, unknown event IDs, dataset version mismatch, invalid confidence, adjudicated-without-reviewer, and synthetic data cards that explicitly reject `real_robot_data=true` without permission metadata.

- [ ] **Step 2: Run red tests**

```powershell
.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_annotations.py -q
```

Expected: collection failure because the annotation module and models do not exist.

- [ ] **Step 3: Implement strict Pydantic contracts**

Use `extra="forbid"`, finite numeric validation, dataset-version equality checks, and stable validation codes. Do not add a path that imports real data automatically; annotations remain an explicit approved input.

- [ ] **Step 4: Verify contracts and document the boundary**

Run the focused pytest and Ruff checks. Write the data card with required provenance, redaction, license/permission, holdout, reviewer, and deletion fields. State that no Unitree/UBTECH/Fourier internal data is included.

---

### Task 3: Business KPI and sim-to-real evaluation layer

**Files:**
- Create: `backend/signalforge/embodied/business_metrics.py`
- Test: `backend/tests/test_embodied_business_metrics.py`
- Create: `eval/embodied_business_cases.jsonl`
- Create: `docs/EMBODIEDOPS_BUSINESS_EVAL.md`
- Modify: `eval/run_embodied_eval.py`

**Interfaces:**
- `BusinessOutcome` fields: `episode_id`, `dataset_version_id`, `before_success`, `after_success`, `human_accepted`, `diagnosis_latency_ms`, `experiment_id`.
- `BusinessMetrics` fields: `success_rate`, `collision_rate`, `timeout_rate`, `diagnosis_accuracy`, `unknown_event_rate`, `human_acceptance_rate`, `median_diagnosis_latency_ms`, `success_uplift`.
- `aggregate_business_metrics(outcomes, annotations) -> BusinessMetrics` preserves explicit numerators/denominators and returns null rates for zero denominators.
- `evaluate_sim_to_real(sim_metrics, real_metrics) -> dict` reports metric deltas and refuses to claim transfer when either side lacks a holdout or permissioned provenance.

- [ ] **Step 1: Write failing metric tests**

Test explicit denominators, null zero-denominator rates, before/after uplift, human acceptance, latency median, and a refusal when real data lacks a holdout or data card permission.

- [ ] **Step 2: Run red tests**

```powershell
.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_business_metrics.py -q
```

- [ ] **Step 3: Implement metrics without model calls**

Compute business outcomes from validated records only. Keep raw model quality, validated system safety, and business outcomes as separate report sections. Never infer ROI from synthetic fixtures.

- [ ] **Step 4: Add fixed offline business cases and report**

Create a small, clearly-labelled synthetic before/after case set. Report `manual_review_status=not_performed` and `real_robot_data=false`; include a “not a production ROI claim” limitation.

- [ ] **Step 5: Verify**

Run the focused tests, existing embodied eval tests, Ruff, and CLI JSON serialization checks.

---

### Task 4: Evidence-chain and trajectory replay UI

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/app/embodied/page.tsx`
- Modify: `frontend/src/app/globals.css`
- Test: `frontend/tests/embodied.test.tsx`

**Interfaces:**
- Add `EmbodiedReplayFrame` and `EmbodiedBusinessMetrics` types.
- Add `getEmbodiedSnapshot(datasetVersionId, signal?)` for a read-only snapshot payload.
- The page renders an event scrubber from existing observations/events, a selected-frame detail panel, and an evidence chain connecting failure phase, supporting events, counter events, unknowns, diagnosis, and experiment.
- No component may call a POST route or expose a robot-control action.

- [ ] **Step 1: Write failing UI tests**

Assert the page shows “现象 → 阶段 → 证据 → 诊断 → 实验”, allows selecting an event, displays event time and severity, and visibly states `simulation_only` and “不控制机器人”.

- [ ] **Step 2: Run red tests**

```powershell
Set-Location frontend
npm.cmd test -- embodied.test.tsx
```

- [ ] **Step 3: Implement the smallest read-only replay**

Use existing episode observations/events and local state only. Keep the timeline deterministic and make unknown/empty evidence visually explicit instead of hiding it.

- [ ] **Step 4: Verify**

Run the focused Vitest, full Vitest, ESLint, and Next production build.

---

### Task 5: Interview and GitHub handoff package

**Files:**
- Modify: `README.md`
- Modify: `docs/CASE_STUDY.md`
- Create: `docs/INTERVIEW_STORY.md`
- Create: `LICENSE`
- Test: `backend/tests/test_public_artifact_safety.py`

**Interfaces:**
- README must contain a cold-start command, `/embodied` route, architecture diagram, data/eval limits, and public-demo safety instructions.
- CASE_STUDY must separate problem, users, workflow, feature engineering, agent boundary, metrics, business KPI, rollout gates, and ROI assumptions.
- INTERVIEW_STORY must provide a 90-second, 5-minute, and deep-dive narrative without claiming real Unitree data or production uplift.
- Public artifact safety test rejects tracked `.env`, DuckDB, model keys, tunnel URLs, and unredacted model response fields.

- [ ] **Step 1: Write failing artifact safety tests**

Scan only repository source/docs/artifacts (excluding `.venv`, `node_modules`, and caches) and fail if secrets, database files, or temporary tunnel URLs are present in publishable paths.

- [ ] **Step 2: Run red tests and inspect findings**

```powershell
.venv\Scripts\python.exe -m pytest backend\tests\test_public_artifact_safety.py -q
```

- [ ] **Step 3: Add handoff docs and license**

Use factual language: synthetic demo, local Ollama optional, approved real-data contract not populated, no production ROI claim. Include exact evidence and commands for reviewers.

- [ ] **Step 4: Verify the public package**

Run artifact safety, all backend tests except opt-in live-model PowerShell checks, all frontend tests, Ruff, ESLint, Next build, and PowerShell parser.

---

## Plan self-review

- Spec coverage: startup bootstrap is Task 1; real-data/annotation boundary is Task 2; business evaluation is Task 3; replay/evidence chain is Task 4; interview/GitHub handoff is Task 5.
- No task imports remote data or controls a robot.
- All rates retain explicit numerators/denominators and all claims distinguish synthetic from real data.
- The only planned write behavior is local fixture bootstrap during read-only startup; public HTTP write routes remain blocked.
