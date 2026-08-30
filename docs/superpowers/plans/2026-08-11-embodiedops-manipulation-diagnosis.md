# EmbodiedOps Manipulation Diagnosis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 SignalForge 的证据链和审计边界之上，增加一个可复现的桌面抓取 episode 失败诊断与策略迭代产品切片。

**Architecture:** 用版本化的 episode/event 数据契约承载仿真和公开真实轨迹；确定性分析层负责阶段、成功率和失败统计；受控 Agent 只基于证据生成根因候选和实验草案；验证器、Trace 和 Next.js 前端共同形成只读可演示闭环。第一版不发送机器人控制指令、不训练策略、不调用云端模型。

**Tech Stack:** Python 3.11、Pydantic 2、DuckDB、FastAPI、pytest、ruff、Next.js 15、React、TypeScript、Vitest、ManiSkill-compatible JSONL/NPZ episode fixtures。

## Global Constraints

- 第一版机器人抽象固定为 6 自由度机械臂 + 两指夹爪，任务固定为桌面抓取与放置。
- 仿真是主数据源；公开真实轨迹只做 schema 和分布校验，不声称拥有 Unitree 生产日志。
- 所有 episode、事件、诊断、实验必须绑定 immutable `dataset_version_id`。
- 任务指标、失败率、阶段耗时、最终状态和安全门禁由服务端确定性规则计算，模型不能覆盖。
- Agent 输出只能引用允许的 episode/event/evidence ID；未知引用、越界时间窗、未知字段和反例遗漏必须拒答或 `needs_evidence`。
- 本地 Ollama 是默认 provider；云端模型、远程 API 和真实机器人控制不在本计划范围。
- 演示模式默认只读；所有写入和真机执行都需要人工确认。
- 当前仓库没有可写 Git HEAD；每个任务仍保留提交边界，但若 Git 权限未恢复，只记录验证结果，不伪造 commit。

---

### Task 1: Episode 数据契约与可复现仿真 fixture

**Files:**
- Create: `backend/signalforge/embodied/__init__.py`
- Create: `backend/signalforge/embodied/models.py`
- Create: `backend/signalforge/embodied/simulator.py`
- Create: `backend/tests/test_embodied_episode_models.py`
- Create: `backend/tests/test_embodied_fixture_generator.py`
- Create: `data/embodied/demo_episode_manifest.json`
- Create: `scripts/generate_embodied_demo.py`

**Interfaces:**
- Produces `Episode`, `EpisodeObservation`, `EpisodeEvent`, `EpisodeOutcome`, `FailureType`, and `DatasetVersionRef` Pydantic models.
- Produces `generate_demo_episodes(*, count: int, seed: int, dataset_version_id: str) -> list[Episode]`.
- Produces JSONL fixture with deterministic seed and manifest containing count, hash, schema_version and failure distribution.

- [ ] **Step 1: Write failing model tests**

```python
def test_episode_rejects_mixed_dataset_versions():
    with pytest.raises(ValidationError):
        Episode(
            episode_id="ep-1",
            dataset_version_id="v1",
            observations=[Observation(dataset_version_id="v2", ...)],
            outcome=EpisodeOutcome(success=False, failure_type="grasp_miss"),
        )

def test_episode_requires_monotonic_timestamps_and_known_failure_type():
    with pytest.raises(ValidationError):
        Episode(..., observations=[Observation(t=1.0), Observation(t=0.5)], ...)
```

- [ ] **Step 2: Run the failing tests**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_episode_models.py -q`

Expected: collection failure because `signalforge.embodied.models` and the episode types do not exist.

- [ ] **Step 3: Implement the minimal contracts**

Use strict Pydantic models with `extra="forbid"`. Required fields:

```python
class Episode(BaseModel):
    episode_id: str
    dataset_version_id: str
    task_id: str
    scene_id: str
    seed: int
    instruction: str
    robot_model: Literal["arm6_gripper"]
    observations: list[EpisodeObservation]
    events: list[EpisodeEvent]
    outcome: EpisodeOutcome

class EpisodeObservation(BaseModel):
    t: float
    joint_positions: list[float] = Field(min_length=6, max_length=6)
    end_effector_pose: list[float] = Field(min_length=7, max_length=7)
    gripper_width: float
    camera_frame_id: str

class EpisodeEvent(BaseModel):
    event_id: str
    t: float
    event_type: Literal["collision", "occlusion", "planner_error", "timeout", "grasp_contact"]
    severity: Literal["info", "warning", "critical"]

class EpisodeOutcome(BaseModel):
    success: bool
    failure_type: FailureType | None
    completion_time_s: float
```

Enforce non-decreasing timestamps, failure type required for unsuccessful episodes, and all nested records sharing the parent dataset version.

- [ ] **Step 4: Implement deterministic fixture generation**

Generate parameterized tabletop pick/place episodes with fixed failure injection for `grasp_miss`, `occlusion`, `collision`, `planner_unreachable`, and `timeout`. The generator must never claim that a simulated episode came from Unitree or a real robot.

- [ ] **Step 5: Run focused tests and validate the manifest**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_episode_models.py backend\tests\test_embodied_fixture_generator.py -q`

Expected: all tests pass; repeated generation with the same seed produces identical JSONL bytes and manifest hash.

- [ ] **Step 6: Commit the isolated task if Git is writable**

```powershell
git add backend/signalforge/embodied backend/tests/test_embodied_episode_models.py backend/tests/test_embodied_fixture_generator.py data/embodied scripts/generate_embodied_demo.py
git commit -m "feat: add versioned embodied episode contract"
```

### Task 2: 阶段切分、特征视图与确定性任务指标

**Files:**
- Create: `backend/signalforge/embodied/phase_analysis.py`
- Create: `backend/signalforge/embodied/metrics.py`
- Create: `backend/tests/test_embodied_phase_analysis.py`
- Create: `backend/tests/test_embodied_metrics.py`

**Interfaces:**
- `segment_episode(episode: Episode) -> list[PhaseWindow]`
- `extract_episode_features(episode: Episode, phases: list[PhaseWindow]) -> EpisodeFeatureView`
- `aggregate_task_metrics(episodes: Sequence[Episode]) -> TaskMetrics`
- `failure_type_distribution(episodes: Sequence[Episode]) -> list[FailureCount]`

- [ ] **Step 1: Write failing phase and metric tests**

Cover exact phase ordering (`approach`, `align`, `grasp`, `transfer`, `place`), event-to-window association, successful completion, timeout, collision rate, and empty dataset behavior.

- [ ] **Step 2: Run tests to verify red**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_phase_analysis.py backend\tests\test_embodied_metrics.py -q`

Expected: import failures for the new functions.

- [ ] **Step 3: Implement deterministic phase rules**

Use timestamps, gripper width changes, end-effector displacement, and event markers. Do not ask the model to invent phase boundaries. Reject windows outside episode duration and preserve the rule name in the result.

- [ ] **Step 4: Implement feature and metric aggregation**

Return explicit denominators and `None` for undefined rates. Include success rate, grasp/placement success rate, collision rate, timeout rate, mean completion time, per-phase failure rate, and failure type counts.

- [ ] **Step 5: Run focused tests and Ruff**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_phase_analysis.py backend\tests\test_embodied_metrics.py -q` and `backend\.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge\embodied backend\tests\test_embodied_phase_analysis.py backend\tests\test_embodied_metrics.py`.

Expected: all tests pass and Ruff reports no errors.

### Task 3: 受控诊断 Agent、证据验证与实验草案

**Files:**
- Create: `backend/signalforge/embodied/diagnosis_models.py`
- Create: `backend/signalforge/embodied/diagnosis.py`
- Create: `backend/signalforge/embodied/experiment.py`
- Create: `backend/tests/test_embodied_diagnosis.py`
- Create: `backend/tests/test_embodied_experiment.py`

**Interfaces:**
- `EpisodeDiagnosisDraft`
- `diagnose_episode(*, episode: Episode, feature_view: EpisodeFeatureView, provider: StructuredModelProvider | None) -> ValidatedDiagnosis`
- `build_reproduction_experiment(diagnosis: ValidatedDiagnosis) -> ReproductionExperiment`
- `validate_diagnosis(draft: Mapping[str, object], *, allowed_event_ids: frozenset[str], episode: Episode) -> ValidationResult`

- [ ] **Step 1: Write failing safety tests**

Test unknown event IDs, unknown sensor fields, timestamps outside episode bounds, unsupported failure types, omitted counter evidence, fabricated numeric values, and explicit refusal when evidence is insufficient.

```python
def test_unknown_event_id_fails_closed():
    result = validate_diagnosis(payload, allowed_event_ids=frozenset({"e1"}), episode=episode)
    assert result.accepted is False
    assert result.code == "UNKNOWN_EVENT_ID"
```

- [ ] **Step 2: Run the safety tests to verify red**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_diagnosis.py backend\tests\test_embodied_experiment.py -q`

Expected: collection failure because diagnosis models and validator do not exist.

- [ ] **Step 3: Implement strict diagnosis schemas**

Allow only `failure_phase`, `root_cause_candidates`, `supporting_event_ids`, `counter_event_ids`, `unknowns`, `confidence_band`, and `decision_status`. Confidence is a categorical band, not a model-generated probability. Numeric task metrics are injected from `TaskMetrics`.

- [ ] **Step 4: Implement provider orchestration**

Use the existing local provider boundary. The prompt receives bounded episode summaries and whitelisted event IDs, not arbitrary database access. A malformed JSON response may retry once; timeout, unavailable provider, unknown citation and validation failures terminate safely with Trace.

- [ ] **Step 5: Implement reproduction experiment builder**

Every experiment must include hypothesis, variable, controlled conditions, minimum sample count, primary success metric, guardrail metric, stop condition, and reassessment rule. Human ownership and real-robot execution remain outside the service.

- [ ] **Step 6: Run focused tests, full backend tests and Ruff**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_diagnosis.py backend\tests\test_embodied_experiment.py -q`, then `backend\.venv\Scripts\python.exe -m pytest backend\tests -q`, then `backend\.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge backend\tests`.

### Task 4: Episode API、持久化与 Trace 查询

**Files:**
- Modify: `backend/signalforge/db/schema.sql`
- Modify: `backend/signalforge/db/repositories.py`
- Create: `backend/signalforge/api/routers/embodied.py`
- Modify: `backend/signalforge/api/app.py`
- Create: `backend/tests/test_embodied_api.py`
- Create: `backend/tests/test_embodied_repositories.py`

**Interfaces:**
- `POST /api/v1/embodied/episodes/import` accepts a versioned JSONL manifest in local/demo mode only.
- `GET /api/v1/embodied/tasks?dataset_version_id=...` returns deterministic task metrics.
- `GET /api/v1/embodied/episodes/{episode_id}` returns the episode, phases, events and outcome.
- `POST /api/v1/embodied/diagnoses` creates a queued read-only diagnosis job.
- `GET /api/v1/embodied/diagnoses/{diagnosis_id}` returns status, diagnosis, validation code and Trace IDs.
- `GET /api/v1/embodied/experiments/{diagnosis_id}` returns the generated reproduction experiment.

- [ ] **Step 1: Write failing repository/API tests**

Cover dataset-version isolation, duplicate episode idempotency, immutable episodes, unknown episode 404, read-only mode rejecting import/diagnosis writes, and trace fields including provider, stage, retry count, latency and validation code.

- [ ] **Step 2: Run tests to verify red**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_api.py backend\tests\test_embodied_repositories.py -q`

Expected: missing table, repository and router failures.

- [ ] **Step 3: Add schema and repository transactions**

Create version-scoped `embodied_episodes`, `embodied_events`, `embodied_diagnoses`, and `embodied_experiments`. Use foreign keys to dataset versions, unique `(dataset_version_id, episode_id)`, immutable episode rows, and atomic diagnosis completion. Do not overwrite a completed diagnosis with a later job.

- [ ] **Step 4: Add API routes and register them**

Reuse current FastAPI dependency and safe error-code patterns. Enforce read-only mode before any write or job creation. Ensure failed jobs never return a partial diagnosis.

- [ ] **Step 5: Run focused API tests and migration tests**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_api.py backend\tests\test_embodied_repositories.py -q`.

Expected: all tests pass with no network or model calls.

### Task 5: 离线评测、真实指标分层与演示快照

**Files:**
- Create: `eval/embodied_gold_cases.jsonl`
- Create: `eval/run_embodied_eval.py`
- Create: `backend/tests/test_embodied_eval.py`
- Create: `docs/EMBODIEDOPS_EVAL_REPORT.md`
- Create: `data/embodied/demo_diagnosis_snapshot.json`

**Interfaces:**
- `evaluate_embodied_diagnosis(gold: GoldCase, prediction: Prediction) -> EmbodiedMetrics`
- `run_embodied_eval(*, fixture_path: Path, output_path: Path) -> EvalReport`
- `EvalReport.raw_model`, `EvalReport.validated_system`, `EvalReport.runtime`, and `EvalReport.limitations` are separate top-level sections.

- [ ] **Step 1: Write failing evaluation tests**

Cover phase accuracy, event evidence precision/coverage, refusal correctness, replay reproducibility, zero persisted unknown event IDs, denominator preservation, JSON serialization, and explicit `manual_review_status="not_performed"` for synthetic cases.

- [ ] **Step 2: Run tests to verify red**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_eval.py -q`

Expected: missing evaluator and gold fixture failures.

- [ ] **Step 3: Implement offline evaluator**

Keep raw provider attempts separate from post-validation system outcomes. Never count a rejected hallucination as a successful model result. Rates must retain numerator and denominator; zero denominators are `null`.

- [ ] **Step 4: Create a labeled demo snapshot**

Generate a deterministic, evidence-bound diagnosis from the synthetic fixture and label it `offline_validation_snapshot`. Include source episode IDs, dataset version, rules version, and limitations. Do not label it as live Qwen output.

- [ ] **Step 5: Run eval and inspect the report**

Run: `backend\.venv\Scripts\python.exe eval\run_embodied_eval.py --fixture eval\embodied_gold_cases.jsonl --output eval\embodied_results.json`.

Expected: machine-readable report plus Markdown report with raw/system separation and no claims of production quality.

### Task 6: Next.js 时间线、诊断和评测页面

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/episode-timeline.tsx`
- Create: `frontend/src/components/failure-diagnosis-panel.tsx`
- Create: `frontend/src/components/experiment-compare.tsx`
- Create: `frontend/src/app/embodied/page.tsx`
- Create: `frontend/tests/embodied-page.test.tsx`
- Create: `frontend/tests/episode-timeline.test.tsx`

**Interfaces:**
- `getEmbodiedTasks(datasetVersionId, signal?)`
- `getEpisode(episodeId, signal?)`
- `getDiagnosis(diagnosisId, signal?)`
- `getExperiment(diagnosisId, signal?)`
- `EpisodeTimeline` renders phase windows, evidence events and selected timestamp.
- `FailureDiagnosisPanel` renders status, candidates, supporting/counter event IDs, unknowns and validation code.
- `ExperimentCompare` renders before/after metrics with explicit denominators.

- [ ] **Step 1: Write failing Vitest tests**

Cover loading/error/empty states, version label, phase ordering, evidence event selection, refusal/needs-evidence display, offline snapshot disclosure, and no robot-control CTA in read-only mode.

- [ ] **Step 2: Run tests to verify red**

Run from `frontend`: `npm.cmd test -- embodied-page.test.tsx episode-timeline.test.tsx`.

Expected: missing route/components/API client failures.

- [ ] **Step 3: Implement typed API clients and components**

Use AbortController and dataset/episode epoch guards. Keep evidence IDs batched and preserve returned order. Render model output only alongside validation state and source event IDs.

- [ ] **Step 4: Build the page around the offline snapshot**

The page must clearly separate `规则任务指标`, `离线验证诊断`, and `实时模型状态`. A failed local model must show failure and limitations, not silently replace the snapshot.

- [ ] **Step 5: Run frontend tests, lint and build**

Run: `npm.cmd test -- --run`, `npm.cmd run lint`, and `npm.cmd run build`.

Expected: all tests pass, lint has zero errors, and the production build includes `/embodied`.

### Task 7: Read-only demo integration and interview handoff

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `README.md`
- Create: `scripts/verify_embodied_demo.ps1`
- Create: `backend/tests/test_embodied_demo_gate.py`

**Interfaces:**
- `verify_embodied_demo.ps1` checks page reachability, dataset version, snapshot disclosure, no robot-control endpoint, and report freshness.
- Compose defaults keep `DEMO_READ_ONLY=true` for public interview builds.

- [ ] **Step 1: Write failing integration tests**

Assert that the read-only build exposes `/embodied`, the page reports dataset version and snapshot mode, generation/control endpoints reject writes, and the verifier requires non-empty episode/task denominators.

- [ ] **Step 2: Run tests to verify red**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_embodied_demo_gate.py -q`.

Expected: missing verifier and compose/page contracts.

- [ ] **Step 3: Wire Compose and verifier**

Keep the same-origin proxy. Never expose Ollama, DuckDB or robot-control ports. The verifier must not call a model or create a diagnosis job; it checks the existing snapshot and read-only response codes.

- [ ] **Step 4: Run complete acceptance checks**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
backend\.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge backend\tests eval
Set-Location frontend; npm.cmd test -- --run; npm.cmd run lint; npm.cmd run build
powershell -ExecutionPolicy Bypass -File scripts\verify_embodied_demo.ps1
```

Expected: all backend/frontend tests, Ruff, production build and read-only demo gates pass. Any real-model timeout is reported separately and cannot make the offline snapshot appear live.

- [ ] **Step 5: Update interview handoff**

Add a three-minute demo script: select dataset version, open mission metrics, inspect one failed episode, trace root cause evidence, open reproduction experiment, compare before/after metrics, and disclose synthetic/public data and model limitations.

## Self-Review Checklist

- Spec coverage: data contract (Task 1), deterministic phase/metrics (Task 2), Agent and safety gates (Task 3), API/persistence/Trace (Task 4), eval and snapshot (Task 5), frontend (Task 6), read-only integration (Task 7).
- No placeholder steps: every task names files, interfaces, tests, commands and expected outcomes.
- Type consistency: `Episode`, `EpisodeFeatureView`, `ValidatedDiagnosis`, `ReproductionExperiment`, `TaskMetrics`, and `EvalReport` are introduced before downstream consumers.
- Scope boundary: no real robot control, policy training, cloud model, or Unitree production-data claim.
