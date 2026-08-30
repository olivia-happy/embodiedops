# SignalForge AI Decision Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local-model, evidence-validated decision-memo pipeline that produces one auditable primary decision or a concrete evidence-collection plan for each dataset version.

**Architecture:** DuckDB and deterministic services remain the only authority for metrics and decision state. A local JSON model adapter performs semantic subproblem grouping and memo drafting, while validators enforce version-scoped citations, redaction rules, counter-evidence, unknowns, and authoritative numbers. Persisted asynchronous jobs expose real generation stages to the Next.js frontend.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, DuckDB, httpx, Next.js 15, React 19, TypeScript, Vitest, pytest, Docker Compose.

## Global Constraints

- 调试阶段只使用本地开源模型，不调用付费 API；最终成品后再次征求用户是否切换。
- 不自动下载大模型，不向远程服务传输评论、证据或凭据。
- DuckDB 与固定规则是全部数值的唯一权威来源，模型不得决定或覆盖数值。
- 每个子问题和判断必须引用当前不可变数据版本中的证据 ID。
- 脱敏证据可计数，但正文不得进入模型上下文，也不能满足非脱敏支持证据门槛。
- 证据不足时拒绝业务建议，只返回可执行补数计划。
- 模型不可用、超时或输出无效时明确失败，不用模板冒充 AI。
- 每个数据版本只保留一份当前有效备忘录；历史生成过程保留 Trace。
- 保持现有证据抽屉、反馈、Trace、风险来源、前端测试和 Docker 验证可用。

---

## File Structure

- Modify `backend/signalforge/core/models.py`: decision memo, evidence reference, experiment, evidence plan, job and extended Trace contracts.
- Modify `backend/signalforge/db/schema.sql`: `decision_memos` and `memo_generation_jobs` tables.
- Modify `backend/signalforge/db/repositories.py`: memo/job persistence and aspect-scoped evidence reads.
- Create `backend/signalforge/services/local_model.py`: local JSON provider protocol, Ollama-compatible adapter and health check.
- Create `backend/signalforge/services/memo_validation.py`: citation, redaction, numeric and state validators.
- Create `backend/signalforge/services/memo_generation.py`: two-stage grouping/memo orchestration.
- Create `backend/signalforge/services/memo_jobs.py`: persisted asynchronous job manager and restart recovery.
- Create `backend/signalforge/api/routers/decision_memos.py`: memo and job endpoints.
- Create `backend/signalforge/api/routers/model_health.py`: safe local-model health endpoint.
- Modify `backend/signalforge/api/schemas.py`, `app.py`, `deps.py`, `core/config.py`, `pyproject.toml`: dependency wiring and HTTP contracts.
- Modify `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`: memo, job and model-health clients.
- Create `frontend/src/components/decision-memo-panel.tsx`: all memo and failure states.
- Create `frontend/src/hooks/use-decision-memo.ts`: start and poll real jobs.
- Modify `frontend/src/app/page.tsx`, `insights/page.tsx`, `decisions/page.tsx`, `components/decision-form.tsx`, `app/globals.css`: memo-first workflow and actionable-only prefill.
- Add focused backend/frontend tests and `eval/gold_decision_memos.jsonl` plus evaluation metrics.

### Task 1: Add decision-memo domain and database schema

**Files:**

- Modify: `backend/signalforge/core/models.py`
- Modify: `backend/signalforge/db/schema.sql`
- Create: `backend/tests/test_decision_memo_models.py`

**Interfaces:**

- Produces: `DecisionMemo`, `MemoFacts`, `MemoEvidenceReference`, `ExperimentPlan`, `EvidencePlan`, `MemoGenerationJob`, `DecisionStatus`, and `MemoJobStatus`.
- Extends: `TraceRecord.entity_type` with `memo`; validation status with `numeric_mismatch` and `failed`.

- [ ] **Step 1: Write failing domain-contract tests**

```python
def test_needs_evidence_memo_requires_evidence_plan() -> None:
    with pytest.raises(ValidationError):
        DecisionMemo(
            id="memo-1",
            dataset_version_id="v1",
            decision_status="needs_evidence",
            decision_statement="暂不采取业务动作",
            topic="service",
            facts=MemoFacts(review_count=4, negative_count=3, negative_rate=75.0),
            supporting_evidence=[],
            counter_evidence=[],
            unknowns=["缺少重复子问题证据"],
            reasoning_summary="现有问题机制分散。",
        )

def test_actionable_memo_requires_experiment() -> None:
    assert DecisionMemo.model_fields["experiment"].annotation is not None
```

- [ ] **Step 2: Run the tests and confirm failure**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_decision_memo_models.py -q`

Expected: FAIL because the decision-memo models do not exist.

- [ ] **Step 3: Implement strict domain models and tables**

```python
DecisionStatus = Literal["actionable", "needs_evidence", "refused"]
MemoJobStatus = Literal[
    "queued", "analyzing_signals", "grouping_evidence",
    "validating_evidence", "generating_memo", "completed", "failed",
]

class MemoEvidenceReference(BaseModel):
    evidence_id: str = Field(min_length=1)
    rationale: str = Field(min_length=1, max_length=300)

class DecisionMemo(BaseModel):
    id: str
    dataset_version_id: str
    decision_status: DecisionStatus
    decision_statement: str
    topic: str
    subproblem: str | None = None
    facts: MemoFacts
    supporting_evidence: list[MemoEvidenceReference]
    counter_evidence: list[MemoEvidenceReference]
    unknowns: list[str]
    reasoning_summary: str
    experiment: ExperimentPlan | None = None
    evidence_plan: EvidencePlan | None = None
    refusal_reason: str | None = None
    model_name: str | None = None
    prompt_version: str
```

Add `decision_memos` with `UNIQUE(dataset_version_id)` and `memo_generation_jobs` with status, memo ID, error code, timestamps and dataset foreign key. Add model validators enforcing state-specific fields.

- [ ] **Step 4: Run model and existing schema tests**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_decision_memo_models.py tests\test_models.py tests\test_repositories.py -q`

Expected: PASS; existing models remain valid.

- [ ] **Step 5: Commit**

Run: `git add backend/signalforge/core/models.py backend/signalforge/db/schema.sql backend/tests/test_decision_memo_models.py; git commit -m "feat: add decision memo domain contracts"`

### Task 2: Persist memos, jobs and aspect-scoped evidence

**Files:**

- Modify: `backend/signalforge/db/repositories.py`
- Modify: `backend/tests/test_repositories.py`

**Interfaces:**

- Consumes: Task 1 domain models.
- Produces: `save_decision_memo`, `get_decision_memo`, `create_memo_job`, `get_memo_job`, `get_running_memo_job`, `update_memo_job`, `fail_incomplete_memo_jobs`, and `list_review_evidence_by_aspect`.

- [ ] **Step 1: Write failing repository tests**

```python
def test_save_decision_memo_replaces_current_version_memo(db, needs_evidence_memo):
    save_decision_memo(db, needs_evidence_memo)
    replacement = needs_evidence_memo.model_copy(update={"id": "memo-2"})
    save_decision_memo(db, replacement)
    assert get_decision_memo(db, "v1").id == "memo-2"

def test_fail_incomplete_jobs_after_restart(db, queued_job):
    create_memo_job(db, queued_job)
    assert fail_incomplete_memo_jobs(db) == 1
    assert get_memo_job(db, queued_job.id).error_code == "SERVICE_RESTARTED"
```

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_repositories.py -q`

Expected: FAIL with missing repository functions.

- [ ] **Step 3: Implement parameterized repository operations**

```python
def save_decision_memo(db: Database, memo: DecisionMemo) -> DecisionMemo:
    db.execute(
        """INSERT INTO decision_memos VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset_version_id) DO UPDATE SET
        id=excluded.id, payload=excluded.payload, status=excluded.status,
        model_name=excluded.model_name, prompt_version=excluded.prompt_version,
        created_at=excluded.created_at""",
        (...),
    )
    return memo

def list_review_evidence_by_aspect(db: Database, version_id: str, aspect: str) -> list[Evidence]:
    rows = db.execute(
        "SELECT id, content, rating, aspect, sentiment, redacted FROM reviews "
        "WHERE dataset_version_id = ? AND aspect = ? ORDER BY id",
        (version_id, aspect),
    ).fetchall()
    return [Evidence(...) for row in rows]
```

Use explicit allowed status transitions in `update_memo_job`; reject backward or terminal-state transitions.

- [ ] **Step 4: Run repository and ETL regression tests**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_repositories.py tests\test_etl.py -q`

Expected: PASS; schema application remains idempotent.

- [ ] **Step 5: Commit**

Run: `git add backend/signalforge/db/repositories.py backend/tests/test_repositories.py; git commit -m "feat: persist decision memos and generation jobs"`

### Task 3: Add the configurable local-model adapter and health check

**Files:**

- Modify: `backend/pyproject.toml`
- Modify: `backend/signalforge/core/config.py`
- Create: `backend/signalforge/services/local_model.py`
- Create: `backend/signalforge/api/routers/model_health.py`
- Modify: `backend/signalforge/api/schemas.py`
- Modify: `backend/signalforge/api/app.py`
- Create: `backend/tests/test_local_model.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**

- Produces: `LocalModelProvider.generate_json(system_prompt, user_prompt)`, `LocalModelProvider.health()`, `LocalModelError(code)`, and `GET /healthz/model`.
- Configuration: `local_model_base_url`, `local_model_name`, `local_model_timeout_seconds`; all optional except positive timeout.

- [ ] **Step 1: Write failing provider and health tests**

```python
@pytest.mark.anyio
async def test_provider_rejects_non_object_json(mock_transport):
    provider = LocalModelProvider(settings, transport=mock_transport('["not-object"]'))
    with pytest.raises(LocalModelError, match="INVALID_MODEL_JSON"):
        await provider.generate_json("system", "user")

def test_model_health_does_not_expose_credentials(client):
    body = client.get("/healthz/model").json()
    assert set(body) == {"configured", "ready", "provider", "model_name", "error_code"}
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_local_model.py tests\test_api.py -q`

Expected: FAIL because provider and route are absent.

- [ ] **Step 3: Implement the no-fallback local adapter**

```python
class LocalModelProvider:
    async def generate_json(self, system_prompt: str, user_prompt: str) -> Mapping[str, object]:
        if not self.settings.local_model_base_url or not self.settings.local_model_name:
            raise LocalModelError("LOCAL_MODEL_UNAVAILABLE")
        response = await self.client.post(
            f"{self.settings.local_model_base_url.rstrip('/')}/api/chat",
            json={"model": self.settings.local_model_name, "stream": False, "format": "json",
                  "messages": [{"role": "system", "content": system_prompt},
                               {"role": "user", "content": user_prompt}]},
        )
        payload = json.loads(response.json()["message"]["content"])
        if not isinstance(payload, dict):
            raise LocalModelError("INVALID_MODEL_JSON")
        return payload
```

Use `httpx.AsyncClient`; map timeout, connection and response errors to stable local error codes. The health route may call provider metadata, but must never return URL query parameters, API keys or prompts.

- [ ] **Step 4: Run provider/API tests and lint**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_local_model.py tests\test_api.py -q; ..\.venv\Scripts\python.exe -m ruff check signalforge tests`

Expected: PASS; unconfigured provider reports `configured=false`, not a generated fallback.

- [ ] **Step 5: Commit**

Run: `git add backend/pyproject.toml backend/signalforge/core/config.py backend/signalforge/services/local_model.py backend/signalforge/api/routers/model_health.py backend/signalforge/api/schemas.py backend/signalforge/api/app.py backend/tests/test_local_model.py backend/tests/test_api.py; git commit -m "feat: add local model provider and health check"`

### Task 4: Validate subproblems, citations, redaction and decision state

**Files:**

- Create: `backend/signalforge/services/memo_validation.py`
- Create: `backend/tests/test_memo_validation.py`

**Interfaces:**

- Produces: `SubproblemDraft`, `GroupingDraft`, `ValidatedGrouping`, `build_authoritative_facts`, `validate_grouping`, `resolve_decision_status`, and `validate_memo_draft`.
- Consumes: `TopicMetric`, opportunity score, `Evidence[]`, and model JSON mappings.

- [ ] **Step 1: Write failing safety-gate tests**

```python
def test_unknown_citation_rejects_whole_grouping(service_evidence):
    draft = grouping({"supporting_evidence_ids": ["made-up-id"]})
    result = validate_grouping(draft, service_evidence)
    assert result.accepted is False
    assert result.reason == "UNKNOWN_EVIDENCE_ID"

def test_redacted_evidence_cannot_satisfy_support_threshold(service_evidence):
    result = validate_grouping(grouping_with_one_visible_and_one_redacted(), service_evidence)
    assert result.validated_subproblems == []

def test_state_is_deterministic(service_metric, dispersed_grouping):
    assert resolve_decision_status(service_metric, dispersed_grouping) == "needs_evidence"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_memo_validation.py -q`

Expected: FAIL because validation service is absent.

- [ ] **Step 3: Implement strict validation**

```python
def validate_grouping(draft: Mapping[str, object], evidence: list[Evidence]) -> ValidationResult:
    parsed = GroupingDraft.model_validate(draft, strict=True)
    by_id = {item.id: item for item in evidence}
    cited = parsed.all_evidence_ids()
    if not cited.issubset(by_id):
        return ValidationResult(False, "UNKNOWN_EVIDENCE_ID", [])
    valid = [
        item for item in parsed.subproblems
        if len({eid for eid in item.supporting_evidence_ids if not by_id[eid].redacted}) >= 2
    ]
    return ValidationResult(True, None, valid)

def resolve_decision_status(metric: TopicMetric, grouping: ValidatedGrouping) -> DecisionStatus:
    if metric.review_count < 3 or not metric.scoreable:
        return "refused"
    return "actionable" if grouping.validated_subproblems else "needs_evidence"
```

`validate_memo_draft` must ignore all model-provided facts, inject `MemoFacts`, require counter-evidence check and require fields appropriate to the computed state.

- [ ] **Step 4: Run safety and scoring tests**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_memo_validation.py tests\test_scoring.py tests\test_analytics.py -q`

Expected: PASS; unsupported numeric rate is zero in test fixtures.

- [ ] **Step 5: Commit**

Run: `git add backend/signalforge/services/memo_validation.py backend/tests/test_memo_validation.py; git commit -m "feat: enforce decision memo evidence gates"`

### Task 5: Orchestrate two-stage decision-memo generation

**Files:**

- Create: `backend/signalforge/services/memo_generation.py`
- Create: `backend/tests/test_memo_generation.py`
- Modify: `backend/signalforge/services/tracing.py`

**Interfaces:**

- Produces: `async generate_decision_memo(db, dataset_version_id, provider, on_stage) -> DecisionMemo`.
- `on_stage(status: MemoJobStatus) -> None` is called only after a real stage begins.
- Consumes: Tasks 2–4 and existing analytics/scoring services.

- [ ] **Step 1: Write failing orchestration tests**

```python
@pytest.mark.anyio
async def test_current_demo_returns_needs_evidence_without_second_model_call(db, fake_provider):
    fake_provider.responses = [grouping_with_distributed_service_problems()]
    memo = await generate_decision_memo(db, "v1", fake_provider, stages.append)
    assert memo.decision_status == "needs_evidence"
    assert fake_provider.call_count == 1
    assert memo.evidence_plan.minimum_evidence_per_subproblem == 3

@pytest.mark.anyio
async def test_actionable_case_calls_memo_stage_and_injects_db_numbers(db, fake_provider):
    fake_provider.responses = [valid_grouping(), memo_draft_with_wrong_numbers()]
    memo = await generate_decision_memo(db, "v1", fake_provider, stages.append)
    assert memo.decision_status == "actionable"
    assert memo.facts.negative_rate == 75.0
    assert fake_provider.call_count == 2
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_memo_generation.py -q`

Expected: FAIL because orchestrator is absent.

- [ ] **Step 3: Implement stage-aware orchestration**

```python
async def generate_decision_memo(db, dataset_version_id, provider, on_stage):
    on_stage("analyzing_signals")
    candidate = select_top_complete_opportunity(db, dataset_version_id)
    evidence = list_review_evidence_by_aspect(db, dataset_version_id, candidate.aspect)
    on_stage("grouping_evidence")
    grouping_json = await retry_once(provider.generate_json, GROUPING_SYSTEM, grouping_prompt(candidate, evidence))
    on_stage("validating_evidence")
    grouping = validate_grouping(grouping_json, evidence)
    status = resolve_decision_status(candidate.metric, grouping)
    if status != "actionable":
        return build_non_actionable_memo(candidate, grouping, status, provider.model_name)
    on_stage("generating_memo")
    memo_json = await retry_once(provider.generate_json, MEMO_SYSTEM, memo_prompt(candidate, grouping, evidence))
    return validate_memo_draft(memo_json, candidate, grouping, evidence, provider.model_name)
```

Prompts must omit redacted content, cap each excerpt, demand JSON only and never request hidden chain-of-thought. Persist accepted/failed memo Trace metadata without storing prompts.

- [ ] **Step 4: Run generation, refusal and tracing tests**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_memo_generation.py tests\test_generation.py tests\test_retrieval.py -q`

Expected: PASS; current-demo fixture uses one model call and produces no experiment.

- [ ] **Step 5: Commit**

Run: `git add backend/signalforge/services/memo_generation.py backend/signalforge/services/tracing.py backend/tests/test_memo_generation.py; git commit -m "feat: generate validated decision memos"`

### Task 6: Add persisted asynchronous jobs and memo API

**Files:**

- Create: `backend/signalforge/services/memo_jobs.py`
- Create: `backend/signalforge/api/routers/decision_memos.py`
- Modify: `backend/signalforge/api/schemas.py`
- Modify: `backend/signalforge/api/app.py`
- Modify: `backend/signalforge/api/deps.py`
- Create: `backend/tests/test_memo_jobs.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**

- Produces: `MemoJobManager.start(job_id)`, `MemoJobManager.run_job(job_id)`, `MemoJobManager.close()`.
- HTTP: `GET /api/v1/decision-memo`, `POST /api/v1/decision-memo/generate`, `GET /api/v1/decision-memo/jobs/{job_id}`.

- [ ] **Step 1: Write failing job and API tests**

```python
def test_generate_returns_existing_running_job(client, running_job):
    response = client.post("/api/v1/decision-memo/generate", json={"dataset_version_id": "v1"})
    assert response.status_code == 202
    assert response.json()["id"] == running_job.id

@pytest.mark.anyio
async def test_job_records_real_stages(job_manager, fake_provider):
    await job_manager.run_job("job-1")
    assert get_memo_job(db, "job-1").status == "completed"
    assert get_decision_memo(db, "v1").decision_status == "needs_evidence"
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_memo_jobs.py tests\test_api.py -q`

Expected: FAIL because manager and routes are absent.

- [ ] **Step 3: Implement task lifecycle and API wiring**

```python
class MemoJobManager:
    def start(self, job_id: str) -> None:
        task = asyncio.create_task(self.run_job(job_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def run_job(self, job_id: str) -> None:
        job = get_memo_job(self.db, job_id)
        try:
            memo = await generate_decision_memo(
                self.db, job.dataset_version_id, self.provider,
                lambda stage: update_memo_job(self.db, job_id, status=stage),
            )
            save_decision_memo(self.db, memo)
            update_memo_job(self.db, job_id, status="completed", memo_id=memo.id)
        except LocalModelError as exc:
            update_memo_job(self.db, job_id, status="failed", error_code=exc.code)
```

App lifespan must call `fail_incomplete_memo_jobs` before accepting traffic and `await manager.close()` on shutdown. The POST endpoint reuses an existing nonterminal job for the same dataset version.

- [ ] **Step 4: Run API and backend regression suites**

Run: `Set-Location backend; ..\.venv\Scripts\python.exe -m pytest tests\test_memo_jobs.py tests\test_api.py tests\test_regression_questions.py -q`

Expected: PASS; failed jobs persist while failed memos do not.

- [ ] **Step 5: Commit**

Run: `git add backend/signalforge/services/memo_jobs.py backend/signalforge/api/routers/decision_memos.py backend/signalforge/api/schemas.py backend/signalforge/api/app.py backend/signalforge/api/deps.py backend/tests/test_memo_jobs.py backend/tests/test_api.py; git commit -m "feat: expose asynchronous decision memo jobs"`

### Task 7: Build the memo-first homepage with real polling

**Files:**

- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/src/hooks/use-decision-memo.ts`
- Create: `frontend/src/components/decision-memo-panel.tsx`
- Modify: `frontend/src/app/page.tsx`
- Modify: `frontend/src/app/globals.css`
- Create: `frontend/tests/decision-memo-panel.test.tsx`
- Modify: `frontend/tests/overview.test.tsx`

**Interfaces:**

- Produces: `DecisionMemo`, `MemoGenerationJob`, `ModelHealth` TypeScript types.
- Produces: `useDecisionMemo(datasetVersionId)` returning `{ memo, job, health, loading, error, generate, retry }`.
- Consumes exact Task 6 API routes; no client-only fake progress.

- [ ] **Step 1: Write failing UI-state tests**

```tsx
render(<DecisionMemoPanel memo={needsEvidenceMemo} job={null} health={readyHealth} onGenerate={vi.fn()} />);
expect(screen.getByText("暂不建议采取业务动作")).toBeVisible();
expect(screen.getByText("每个候选子问题至少 3 条独立证据")).toBeVisible();
expect(screen.queryByRole("button", { name: "创建实验" })).not.toBeInTheDocument();

render(<DecisionMemoPanel memo={null} job={{ status: "validating_evidence" }} health={readyHealth} onGenerate={vi.fn()} />);
expect(screen.getByText("正在验证证据")).toBeVisible();
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `Set-Location frontend; npm test -- decision-memo-panel.test.tsx overview.test.tsx`

Expected: FAIL because hook and panel are absent.

- [ ] **Step 3: Implement API client, polling hook and panel**

```ts
export async function startDecisionMemo(datasetVersionId: string): Promise<MemoGenerationJob> {
  return requestJson(`${apiBaseUrl}/api/v1/decision-memo/generate`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ dataset_version_id: datasetVersionId }),
  });
}

useEffect(() => {
  if (!job || ["completed", "failed"].includes(job.status)) return;
  const timer = window.setInterval(() => void refreshJob(job.id), 800);
  return () => window.clearInterval(timer);
}, [job]);
```

Map server statuses to exact Chinese labels. Display model-unavailable, failed, refused, needs-evidence and actionable states distinctly. Replace the current homepage hero with the memo panel while retaining deterministic baseline metrics and risks below it.

- [ ] **Step 4: Run frontend focused tests and lint**

Run: `Set-Location frontend; npm test -- decision-memo-panel.test.tsx overview.test.tsx; npm run lint`

Expected: PASS; no “创建实验” action appears for non-actionable memos.

- [ ] **Step 5: Commit**

Run: `git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/hooks/use-decision-memo.ts frontend/src/components/decision-memo-panel.tsx frontend/src/app/page.tsx frontend/src/app/globals.css frontend/tests/decision-memo-panel.test.tsx frontend/tests/overview.test.tsx; git commit -m "feat: present the validated decision memo workflow"`

### Task 8: Connect evidence exploration and actionable-only experiment prefill

**Files:**

- Modify: `frontend/src/app/insights/page.tsx`
- Modify: `frontend/src/components/insights-body.tsx`
- Modify: `frontend/src/app/decisions/page.tsx`
- Modify: `frontend/src/components/decision-form.tsx`
- Modify: `frontend/tests/insights.test.tsx`
- Modify: `frontend/tests/decisions.test.tsx`

**Interfaces:**

- Consumes: current `DecisionMemo` and existing `getEvidence`/`createDecision`.
- Produces: `DecisionForm({ datasetVersionId, onCreated, initialValues? })`; prefill is accepted only from `actionable` memo.

- [ ] **Step 1: Write failing evidence/prefill tests**

```tsx
render(<InsightsBody metrics={metrics} insights={[]} memo={actionableMemo} onShowEvidence={showEvidence} />);
await user.click(screen.getByRole("button", { name: "查看支持证据" }));
expect(showEvidence).toHaveBeenCalledWith(actionableMemo.supporting_evidence.map(item => item.evidence_id));

render(<DecisionForm datasetVersionId="v1" initialValues={memoDefaults(actionableMemo)} onCreated={vi.fn()} />);
expect(screen.getByLabelText("假设")).toHaveValue(actionableMemo.experiment.hypothesis);
```

- [ ] **Step 2: Run tests and confirm failure**

Run: `Set-Location frontend; npm test -- insights.test.tsx decisions.test.tsx`

Expected: FAIL because memo evidence and prefill are not connected.

- [ ] **Step 3: Implement memo-driven evidence and form defaults**

```tsx
const initialValues = memo?.decision_status === "actionable" && memo.experiment ? {
  title: memo.subproblem ?? memo.topic,
  evidence_ids: memo.supporting_evidence.map(item => item.evidence_id),
  problem_statement: memo.decision_statement,
  hypothesis: memo.experiment.hypothesis,
  primary_metric: memo.experiment.primary_metric,
  guardrail_metric: memo.experiment.guardrail_metric,
} : undefined;
```

Use `defaultValue` and a key based on memo ID for uncontrolled form fields. “用户洞察” shows the memo’s supporting/counter evidence groups without generating a second conclusion. `needs_evidence` and `refused` memos never produce defaults.

- [ ] **Step 4: Run feature and regression tests**

Run: `Set-Location frontend; npm test -- insights.test.tsx decisions.test.tsx evidence-drawer.test.tsx; npm run lint`

Expected: PASS; evidence drawer and manual form entry remain available.

- [ ] **Step 5: Commit**

Run: `git add frontend/src/app/insights/page.tsx frontend/src/components/insights-body.tsx frontend/src/app/decisions/page.tsx frontend/src/components/decision-form.tsx frontend/tests/insights.test.tsx frontend/tests/decisions.test.tsx; git commit -m "feat: connect memo evidence and experiment prefill"`

### Task 9: Add decision-quality evaluation and local setup verification

**Files:**

- Create: `eval/gold_decision_memos.jsonl`
- Modify: `eval/run_eval.py`
- Modify: `docs/EVAL_REPORT.md`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Create: `scripts/verify_model.ps1`
- Modify: `scripts/verify_local.ps1`

**Interfaces:**

- Produces evaluation keys: `subproblem_evidence_precision`, `citation_completeness`, `unsupported_numeric_rate`, `decision_status_accuracy`, `evidence_plan_specificity`.
- Produces a model verification script that reads health only and never pulls a model.

- [ ] **Step 1: Add failing evaluation expectations**

```python
def test_decision_memo_eval_has_quality_metrics():
    result = evaluate_decision_memos(GOLD_MEMO_PATH)
    assert result["unsupported_numeric_rate"] == 0.0
    assert result["decision_status_accuracy"] >= 0.8
    assert set(result) >= {
        "subproblem_evidence_precision", "citation_completeness",
        "unsupported_numeric_rate", "decision_status_accuracy",
        "evidence_plan_specificity",
    }
```

- [ ] **Step 2: Run evaluation tests and confirm failure**

Run: `Set-Location D:\yuanjing\agent\project1; .\.venv\Scripts\python.exe -m pytest backend\tests\test_regression_questions.py -q; .\.venv\Scripts\python.exe eval\run_eval.py`

Expected: FAIL or missing decision-quality keys before implementation.

- [ ] **Step 3: Implement fixtures, metrics and no-download setup**

Create reviewed cases covering actionable, distributed subproblems, refusal, counter-evidence, redaction, unknown citations and numeric mismatch. Add environment keys:

```dotenv
LOCAL_MODEL_BASE_URL=http://host.docker.internal:11434
LOCAL_MODEL_NAME=
LOCAL_MODEL_TIMEOUT_SECONDS=60
```

Add Docker host-gateway access where required. `verify_model.ps1` calls `/healthz/model` and reports configured/ready/model/error; it must never install software or pull a model. Update the evaluation report to distinguish regression metrics from production claims.

- [ ] **Step 4: Run complete automated verification**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest -q backend\tests
.\.venv\Scripts\python.exe -m ruff check backend\signalforge backend\tests eval
.\.venv\Scripts\python.exe eval\run_eval.py
Set-Location frontend
npm test
npm run lint
npm run build
Set-Location ..
powershell -ExecutionPolicy Bypass -File .\scripts\verify_local.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\verify_model.ps1
```

Expected: backend/frontend suites and build pass; local verification passes; model verification either reports a ready configured local model or the explicit nonfatal `LOCAL_MODEL_UNAVAILABLE` state.

- [ ] **Step 5: Commit**

Run: `git add eval/gold_decision_memos.jsonl eval/run_eval.py docs/EVAL_REPORT.md .env.example docker-compose.yml scripts/verify_model.ps1 scripts/verify_local.ps1; git commit -m "test: evaluate decision memo quality"`

## Plan Self-Review

- **Spec coverage:** Tasks 1–2 cover domain and persistence; Task 3 covers local-only provider and honest availability; Tasks 4–5 cover evidence grouping, redaction, deterministic state, two-stage generation and no fake AI; Task 6 covers persistent real-stage jobs and restart recovery; Tasks 7–8 cover memo-first UI, evidence exploration and actionable-only experiment prefill; Task 9 covers decision-quality metrics, Docker connectivity and no-download local-model verification.
- **Placeholder scan:** Every task contains concrete files, interfaces, failing tests, implementation signatures, commands, expected outcomes and a scoped commit. No unfinished markers or deferred implementation instructions remain.
- **Type consistency:** Backend and frontend both use `actionable | needs_evidence | refused` and the same seven nonterminal/terminal job statuses. `DecisionMemo` evidence references use `evidence_id`; `MemoJobManager` consumes the same job model exposed by the API; frontend polling reads the exact job endpoint created in Task 6.
- **Safety consistency:** Provider errors never call the old deterministic insight fallback. Model text never enters numeric facts. Redacted text is excluded before prompt construction. Paid providers and automatic model downloads remain outside this plan.
