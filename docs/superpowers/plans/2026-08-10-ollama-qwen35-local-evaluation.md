# Ollama Qwen3.5 Local Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run `qwen3.5:9b` entirely through local Ollama, harden deterministic structured generation, and produce an honest real-model report that separates raw model behavior from validated system safety.

**Architecture:** Windows Ollama serves the pinned model on `localhost:11434`; the Docker API reaches it only through `host.docker.internal`. The existing two-stage memo orchestrator remains the production path, while a recording wrapper converts each transient raw payload into counts and stable codes before discarding the payload. Offline synthetic gates remain separate from the opt-in real-model evaluation.

**Tech Stack:** Ollama 0.32.6, Qwen3.5 9B Q4_K_M, Python 3.11, FastAPI, Pydantic 2, DuckDB, httpx, PowerShell, pytest, Docker Compose.

## Global Constraints

- Only local `http://localhost:11434` or `http://host.docker.internal:11434` is permitted; no cloud model, external API, subscription, or API key.
- Any later paid or remote provider requires fresh user approval.
- Pin `LOCAL_MODEL_NAME=qwen3.5:9b`, context length `16384`, temperature `0`, seed `42`, maximum output `2048`, thinking enabled, and timeout `120` seconds.
- Pass a Pydantic-derived JSON Schema to Ollama and still run all existing Pydantic/domain validation after generation.
- DuckDB and deterministic services remain the only authority for numbers, decision status, evidence scope, and experiment control fields.
- Never persist prompts, model prose, reasoning content, review excerpts, credentials, or raw response envelopes in evaluation artifacts.
- Raw-model denominators and validated-system denominators must be separate; zero denominators serialize as `null`, never as a fabricated zero rate.
- The existing `eval/run_eval.py` remains offline and must not start or call Ollama.
- The repository has an initialized `.git` directory but no initial commit and nearly all project files are untracked. Do not create a misleading partial first commit; use test results and `git diff --check` as checkpoints.

---

## File Structure

- Modify `backend/signalforge/core/config.py`: deterministic local-generation settings and validation.
- Modify `backend/signalforge/services/local_model.py`: schema-aware request, Ollama usage metadata, and pinned model metadata.
- Modify `backend/signalforge/services/memo_validation.py`: public draft schemas and numeric-text audit helper.
- Modify `backend/signalforge/services/memo_generation.py`: pass the exact stage schema to the provider without changing deterministic validation.
- Modify `backend/tests/test_config.py`, `test_local_model.py`, `test_memo_generation.py`, `test_memo_jobs.py`, and `test_api.py`: provider protocol and reproducibility contracts.
- Create `eval/local_model_metrics.py`: pure raw/system metric calculations with explicit numerators and denominators.
- Create `eval/live_model_cases.jsonl`: three synthetic, non-sensitive real-model evaluation cases.
- Create `eval/run_local_model_eval.py`: opt-in production-orchestrator runner and privacy-safe report writer.
- Create `backend/tests/test_local_model_eval.py`: evaluation, privacy, denominator, retry, and report tests using fake providers only.
- Create `scripts/verify_real_model.ps1`: explicit expensive/live verification separate from fast offline verification.
- Modify `frontend/src/lib/api.ts` and create `frontend/tests/api.test.ts`: load memo evidence in bounded batches of eight.
- Modify `.env.example` and `docker-compose.yml`: local-only pinned defaults; remove unused remote LLM environment examples.
- Create local ignored `.env` during execution; it contains only the approved local settings.
- Generate `eval/local_model-results.json` and `docs/LOCAL_MODEL_EVAL_REPORT.md` only after running the real model.

### Task 1: Deterministic schema-aware local transport

**Files:**

- Modify: `backend/signalforge/core/config.py`
- Modify: `backend/signalforge/services/local_model.py`
- Modify: `backend/signalforge/services/memo_validation.py`
- Modify: `backend/signalforge/services/memo_generation.py`
- Modify: `backend/tests/test_config.py`
- Modify: `backend/tests/test_local_model.py`
- Modify: `backend/tests/test_memo_generation.py`
- Modify: `backend/tests/test_memo_jobs.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**

- `StructuredModelProvider.generate_json(system_prompt: str, user_prompt: str, response_schema: Mapping[str, object] | None = None) -> Mapping[str, object]`
- `LocalModelProvider.generate_json_with_metadata(...) -> LocalModelGeneration`
- `LocalModelProvider.model_metadata() -> LocalModelMetadata`
- `grouping_response_schema() -> dict[str, object]`
- `memo_response_schema() -> dict[str, object]`

- [ ] **Step 1: Write failing configuration and request-contract tests**

Add tests that instantiate `Settings()` and assert the exact defaults, then capture the `/api/chat` body with `httpx.MockTransport`:

```python
assert settings.local_model_context_length == 16384
assert settings.local_model_timeout_seconds == 120.0
assert settings.local_model_temperature == 0.0
assert settings.local_model_seed == 42
assert settings.local_model_max_output_tokens == 2048
assert settings.local_model_think is True

assert body["format"] == response_schema
assert body["think"] is True
assert body["options"] == {
    "num_ctx": 16384,
    "temperature": 0.0,
    "seed": 42,
    "num_predict": 2048,
}
```

Add invalid-boundary tests for context below `4096`, temperature above `2`, negative seed, and output below `256`.

- [ ] **Step 2: Run the focused tests and confirm red status**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1\backend
..\.venv\Scripts\python.exe -m pytest tests\test_config.py tests\test_local_model.py tests\test_memo_generation.py tests\test_memo_jobs.py tests\test_api.py -q
```

Expected: failures for missing settings, missing optional schema argument, and the old `format: "json"` body.

- [ ] **Step 3: Add strict settings and public stage schemas**

Add these `Settings` fields:

```python
local_model_context_length: int = Field(default=16384, ge=4096, le=262144)
local_model_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
local_model_seed: int = Field(default=42, ge=0)
local_model_max_output_tokens: int = Field(default=2048, ge=256, le=8192)
local_model_think: bool = True
```

Also change the existing `local_model_timeout_seconds` default from `60.0` to `120.0`; update all tests and delivery assertions that intentionally check this default.

Rename `_MemoDraft` to `MemoDraft`, update internal references, and expose schemas without exposing private implementation names:

```python
def grouping_response_schema() -> dict[str, object]:
    return GroupingDraft.model_json_schema()

def memo_response_schema() -> dict[str, object]:
    return MemoDraft.model_json_schema()
```

- [ ] **Step 4: Implement the optional schema argument and deterministic Ollama body**

Keep callers that pass two arguments compatible, but use the supplied schema for production stages:

```python
async def generate_json(
    self,
    system_prompt: str,
    user_prompt: str,
    response_schema: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    generation = await self.generate_json_with_metadata(
        system_prompt, user_prompt, response_schema=response_schema
    )
    return generation.payload
```

The `/api/chat` JSON must include:

```python
{
    "model": self.model_name,
    "stream": False,
    "format": dict(response_schema) if response_schema is not None else "json",
    "think": self.settings.local_model_think,
    "options": {
        "num_ctx": self.settings.local_model_context_length,
        "temperature": self.settings.local_model_temperature,
        "seed": self.settings.local_model_seed,
        "num_predict": self.settings.local_model_max_output_tokens,
    },
    "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
}
```

`memo_generation.py` must call the first stage with `grouping_response_schema()` and the actionable draft stage with `memo_response_schema()`. Update every fake provider to accept the optional third argument and assert that the two received schema titles are `GroupingDraft` and `MemoDraft`.

- [ ] **Step 5: Parse usage and model metadata without retaining content**

Add frozen data classes:

```python
@dataclass(frozen=True)
class LocalModelUsage:
    prompt_eval_count: int | None
    eval_count: int | None
    total_duration_ns: int | None
    load_duration_ns: int | None
    prompt_eval_duration_ns: int | None
    eval_duration_ns: int | None

@dataclass(frozen=True)
class LocalModelGeneration:
    payload: Mapping[str, object]
    usage: LocalModelUsage
    response_model: str | None

@dataclass(frozen=True)
class LocalModelMetadata:
    tag: str
    digest: str
    size_bytes: int
    parameter_size: str | None
    quantization: str | None
    format: str | None
    family: str | None
    license_id: str | None
    ollama_version: str | None
    loaded_size_vram: int | None
    loaded_context_length: int | None
```

Parse numeric timing/token fields only when they are non-negative integers. `model_metadata()` must query only the already validated local base URL: `/api/tags`, `/api/version`, `POST /api/show`, and optional `/api/ps`; select the exact configured tag or its `:latest` alias and fail with existing stable codes on malformed required data. Normalize a `/api/show` license containing `Apache License Version 2.0` to `Apache-2.0` and do not persist the full license text. Never return `message`, `thinking`, `content`, prompts, or server error bodies.

- [ ] **Step 6: Run focused and full backend regression**

Run the focused command from Step 2, then:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge backend\tests
git -C D:\yuanjing\agent\project1 diff --check
```

Expected: all backend tests and Ruff pass; no network request occurs because provider tests use `MockTransport`.

### Task 2: Privacy-safe raw and validated metrics

**Files:**

- Modify: `backend/signalforge/services/memo_validation.py`
- Create: `eval/local_model_metrics.py`
- Create: `backend/tests/test_local_model_eval.py`

**Interfaces:**

- `contains_model_numeric_claim(value: object, *, field_name: str | None = None) -> bool`
- `audit_grouping_payload(...) -> RawAttemptAssessment`
- `audit_memo_payload(...) -> RawAttemptAssessment`
- `ratio(numerator: int, denominator: int) -> Ratio`
- `summarize_attempts(...) -> dict[str, object]`

- [ ] **Step 1: Write failing pure-metric tests**

Cover these exact cases:

```python
assert ratio(0, 0).rate is None
assert ratio(1, 4).rate == 0.25
assert audit.unknown_citation_count == 1
assert audit.counter_evidence_omission_count == 1
assert audit.numeric_text_detected is True
assert audit.mechanism_rubric_passed is False
```

Also serialize the final result recursively and reject any key containing one of: `prompt`, `messages`, `response`, `raw`, `content`, `excerpt`, `api_key`, `thinking`. Add a regression where every run is refused: safety counts remain zero while `effective_output_rate.rate == 0.0`.

- [ ] **Step 2: Run the metric tests and confirm red status**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest backend\tests\test_local_model_eval.py -q
```

Expected: import failures because the metric module and public numeric helper do not exist.

- [ ] **Step 3: Expose the production numeric detector and implement explicit ratios**

Rename `_contains_model_numeric_claim` to `contains_model_numeric_claim` and update production callers. Implement:

```python
@dataclass(frozen=True)
class Ratio:
    numerator: int
    denominator: int
    rate: float | None

def ratio(numerator: int, denominator: int) -> Ratio:
    if numerator < 0 or denominator < 0 or numerator > denominator:
        raise ValueError("invalid ratio")
    return Ratio(numerator, denominator, None if denominator == 0 else round(numerator / denominator, 3))
```

- [ ] **Step 4: Implement stage-specific raw audits**

`audit_grouping_payload` must parse with `GroupingDraft.model_validate`, count citations only from the payload, and use only counter IDs actually included in that stage's prompt as the raw-model omission denominator. `audit_memo_payload` must parse with `MemoDraft.model_validate`, audit support/counter citations separately, and use `contains_model_numeric_claim`.

The mechanism rubric is deterministic and explicitly heuristic: normalized mechanism is non-empty, differs from the subproblem name and every normalized evidence excerpt, contains at least 12 Chinese characters, and contains one of `因为、由于、导致、使得、从而、环节、路径、触发、缺少、无法`. Store only the boolean and counts; do not store the mechanism or excerpts.

- [ ] **Step 5: Keep raw and system summaries independent**

The raw section must contain explicit `Ratio` objects for first-pass JSON object, first-pass domain schema, after-one-retry schema, unknown citations, prompt-visible counter omissions, numeric-text blocks, and mechanism rubric. The system section must contain terminal distribution, effective output, persisted unknown citations, unsupported numeric claims, redacted references, evidence-link integrity, and dataset-version integrity. `needs_evidence` counts as an effective evidence-safe output; `refused` and `failed` remain visible and do not count as effective.

- [ ] **Step 6: Run pure tests, full backend tests, and Ruff**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest backend\tests\test_local_model_eval.py backend\tests\test_memo_validation.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge backend\tests eval
git -C D:\yuanjing\agent\project1 diff --check
```

Expected: all tests and lint pass with no live model call.

### Task 3: Opt-in production-orchestrator evaluation runner

**Files:**

- Create: `eval/live_model_cases.jsonl`
- Create: `eval/run_local_model_eval.py`
- Modify: `backend/tests/test_local_model_eval.py`

**Interfaces:**

- `RecordingProvider.generate_json(...) -> Mapping[str, object]`
- `evaluate_local_model(cases_path: Path, provider: LocalModelProvider, *, repeats: int) -> dict[str, object]`
- `render_markdown_report(result: Mapping[str, object]) -> str`

- [ ] **Step 1: Add three non-sensitive fixed cases**

Use these case intents and complete review rows in JSONL:

```json
{"id":"coherent-service","expected_status":"actionable","reviews":[{"id":"c1-n1","text":"午高峰排队时看不到当前位置，顾客会反复询问进度。","rating":1,"aspect":"service","sentiment":"negative","redacted":false},{"id":"c1-n2","text":"等待过程中缺少预计完成提示，顾客无法安排后续时间。","rating":2,"aspect":"service","sentiment":"negative","redacted":false},{"id":"c1-n3","text":"订单处理中没有阶段反馈，容易触发重复联系客服。","rating":2,"aspect":"service","sentiment":"negative","redacted":false},{"id":"c1-p1","text":"部分门店已经展示排队位置，进度查询明显更顺畅。","rating":4,"aspect":"service","sentiment":"positive","redacted":false}]}
{"id":"split-service","expected_status":"needs_evidence","reviews":[{"id":"c2-n1","text":"排队页面缺少当前位置。","rating":2,"aspect":"service","sentiment":"negative","redacted":false},{"id":"c2-n2","text":"客服首次响应太慢。","rating":2,"aspect":"service","sentiment":"negative","redacted":false},{"id":"c2-n3","text":"退款说明难以理解。","rating":2,"aspect":"service","sentiment":"negative","redacted":false},{"id":"c2-p1","text":"订单进度提示很清楚。","rating":4,"aspect":"service","sentiment":"positive","redacted":false}]}
{"id":"weak-positive","expected_status":"refused","reviews":[{"id":"c3-p1","text":"服务流程清楚。","rating":5,"aspect":"service","sentiment":"positive","redacted":false},{"id":"c3-p2","text":"客服回复及时。","rating":5,"aspect":"service","sentiment":"positive","redacted":false},{"id":"c3-p3","text":"排队提示容易理解。","rating":4,"aspect":"service","sentiment":"positive","redacted":false}]}
```

- [ ] **Step 2: Write runner tests with fake provider sequences**

Tests must prove that the runner calls the real `generate_decision_memo()` function against a fresh temporary DuckDB per case/repeat, exercises both grouping and actionable memo schemas, records retry events, catches `LocalModelError` and `MemoGenerationError` as stable codes, and never retains raw payload text after assessment.

- [ ] **Step 3: Run runner tests and confirm red status**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest backend\tests\test_local_model_eval.py -q
```

Expected: failures because the runner and fixture do not exist.

- [ ] **Step 4: Implement the serial recording runner**

For each repeat, create a unique temporary DuckDB, insert one immutable dataset version and its reviews, then call production `generate_decision_memo()`. `RecordingProvider` identifies the stage from the schema title, calls `generate_json_with_metadata`, immediately converts the payload to `RawAttemptAssessment`, and drops the payload reference after returning it to the orchestrator. The runner must not write the temporary database outside its temporary directory.

Use `perf_counter()` for cold/warm end-to-end timing. Use Ollama's actual token/duration fields when present; store `null` when absent. Existing Trace `token_estimate` must be labeled `input_token_estimate`, not actual token usage. A generation-stage Trace `accepted` must be labeled `json_object_received`; only the independent raw audit may claim domain-schema success.

- [ ] **Step 5: Render strict JSON and Markdown outputs**

The runner pins the official release metadata for the only approved model as `release_date: "2026-03-02"` and `release_source: "https://github.com/QwenLM/Qwen3.6"`; an unknown model tag is rejected rather than assigned invented release metadata. The JSON top level is exactly:

```python
{
    "evaluation_kind": "live_local_model_synthetic_cases",
    "generated_at": "<UTC ISO timestamp generated at runtime>",
    "model_artifact": {...},
    "configuration": {...},
    "case_count": 3,
    "repeat_count": repeats,
    "raw_model": {...},
    "validated_system": {...},
    "runtime": {...},
    "case_results": [...],
    "limitations": [
        "固定合成案例，不是生产流量。",
        "机制深度是透明启发式，不是独立人工评分。",
        "结果只适用于记录的模型 digest、量化、配置与硬件。",
    ],
}
```

The Markdown report must repeat those limitations, show every numerator/denominator, call out failures and refusals, and state `manual_review_status: not_performed`. It must not claim independent review, production quality, or business impact.

- [ ] **Step 6: Run tests and lint**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest backend\tests\test_local_model_eval.py -q
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge backend\tests eval
git -C D:\yuanjing\agent\project1 diff --check
```

Expected: all tests and lint pass; no test accesses Ollama.

### Task 4: Explicit real-model verification and local delivery configuration

**Files:**

- Create: `scripts/verify_real_model.ps1`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `backend/tests/test_local_model_eval.py`

**Interfaces:**

- `verify_real_model.ps1 -ExpectedModel qwen3.5:9b -Repeats 3 -RequestTimeoutSeconds 120`

- [ ] **Step 1: Write failing delivery and PowerShell contract tests**

Assert that `.env.example` and Compose carry the exact eight local-model environment settings listed below and contain no `LLM_BASE_URL`, `LLM_MODEL`, or `LLM_API_KEY`. Mock PowerShell HTTP functions and the Python runner so tests cover: health not ready, wrong model, stale output, missing digest, absent raw/system sections, nonzero persisted safety counts, zero terminal denominator, and a valid report.

- [ ] **Step 2: Run the tests and confirm red status**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest backend\tests\test_local_model_eval.py -q
```

Expected: failures because the verifier and pinned environment settings do not exist.

- [ ] **Step 3: Update local-only example and Compose values**

Use these exact values:

```dotenv
DATABASE_PATH=
LOCAL_MODEL_BASE_URL=http://host.docker.internal:11434
LOCAL_MODEL_NAME=qwen3.5:9b
LOCAL_MODEL_TIMEOUT_SECONDS=120
LOCAL_MODEL_CONTEXT_LENGTH=16384
LOCAL_MODEL_TEMPERATURE=0
LOCAL_MODEL_SEED=42
LOCAL_MODEL_MAX_OUTPUT_TOKENS=2048
LOCAL_MODEL_THINK=true
```

Pass the same variables into the API container. Keep `extra_hosts: host.docker.internal:host-gateway` and do not add an Ollama container or any remote hostname.

- [ ] **Step 4: Implement the explicit live verifier**

`verify_real_model.ps1` must first require `/healthz/model` to return `configured=true`, `ready=true`, and the exact expected model. It then invokes `eval/run_local_model_eval.py`, checks that the JSON output is newer than the runner and cases file, requires a non-empty digest, verifies raw/system/runtime sections, requires all three persisted safety counts to equal zero, and requires a nonzero job denominator. It prints only a compact sanitized summary.

Do not call this script from `verify_local.ps1`; real evaluation is opt-in and can take minutes.

- [ ] **Step 5: Run mocked PowerShell tests, syntax checks, backend tests, and Ruff**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest backend\tests\test_local_model_eval.py backend\tests\test_eval_decision_memos.py -q
$errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path .\scripts\verify_real_model.ps1),[ref]$null,[ref]$errors); if($errors){$errors | ForEach-Object { Write-Error $_ }; exit 1}
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge backend\tests eval
git -C D:\yuanjing\agent\project1 diff --check
```

Expected: all tests, syntax checks, and lint pass without a live model call.

### Task 5: Make every memo evidence link loadable

**Files:**

- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/tests/api.test.ts`

**Interfaces:**

- `getEvidence(datasetVersionId: string, ids: string[], signal?: AbortSignal) -> Promise<Evidence[]>`

- [ ] **Step 1: Write failing API-client tests for nine or more evidence IDs**

Mock `fetch`, call `getEvidence("v1", ["e9", "e1", ..., "e9", "e10"])`, and assert that requests contain at most eight `evidence_id` query values, the dataset version is present in every request, duplicates are requested once, and merged results preserve first-requested ID order. Add failure and abort-signal tests: any failed batch rejects the whole call, and the same `AbortSignal` reaches every fetch.

- [ ] **Step 2: Run the focused test and confirm red status**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1\frontend
npm test -- --run tests/api.test.ts
```

Expected: failure because the existing client sends every ID in one request and has no signal argument.

- [ ] **Step 3: Implement bounded deterministic batching**

Use this behavior:

```typescript
const uniqueIds = [...new Set(ids)];
const batches = Array.from(
  { length: Math.ceil(uniqueIds.length / 8) },
  (_, index) => uniqueIds.slice(index * 8, index * 8 + 8),
);
const responses = await Promise.all(batches.map((batch) => fetchEvidenceBatch(datasetVersionId, batch, signal)));
const byId = new Map(responses.flat().map((item) => [item.id, item]));
return uniqueIds.flatMap((id) => byId.has(id) ? [byId.get(id)!] : []);
```

Return `[]` without issuing a request when `ids` is empty. `fetchEvidenceBatch` must retain the existing user-safe error message and pass `{ cache: "no-store", signal }`.

- [ ] **Step 4: Run focused and full frontend verification**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1\frontend
npm test -- --run tests/api.test.ts
npm test -- --run
npm run lint
npm run build
```

Expected: all frontend tests, ESLint, type checking, and production build pass.

### Task 6: Download, configure, run, and document the real model

**Files:**

- Create locally ignored: `.env`
- Generate: `eval/local_model-results.json`
- Generate: `docs/LOCAL_MODEL_EVAL_REPORT.md`
- Modify only if measured facts require correction: `docs/CASE_STUDY.md`

**Interfaces:**

- Host Ollama: `C:\Users\yuanjing\AppData\Local\Programs\Ollama\ollama.exe`
- Docker CLI: `C:\Program Files\Docker\Docker\resources\bin\docker.exe`
- Production dataset: `GET http://localhost:8000/api/v1/overview`

- [ ] **Step 1: Verify free disk and current empty model state**

Run:

```powershell
[System.IO.DriveInfo]::new('C').AvailableFreeSpace / 1GB
& 'C:\Users\yuanjing\AppData\Local\Programs\Ollama\ollama.exe' --version
& 'C:\Users\yuanjing\AppData\Local\Programs\Ollama\ollama.exe' list
```

Expected before download: at least 15GB free, Ollama `0.32.6` or newer, and no existing conflicting `qwen3.5:9b` digest.

- [ ] **Step 2: Pull the approved free local model**

Run:

```powershell
& 'C:\Users\yuanjing\AppData\Local\Programs\Ollama\ollama.exe' pull qwen3.5:9b
& 'C:\Users\yuanjing\AppData\Local\Programs\Ollama\ollama.exe' list
```

Expected: `qwen3.5:9b` appears locally at about 6.6GB. This command does not use a cloud inference model or paid API.

- [ ] **Step 3: Create the ignored local environment file and rebuild API**

Create `.env` with the exact values from Task 4. Then run:

```powershell
& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' compose -f D:\yuanjing\agent\project1\docker-compose.yml up --build -d
& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' compose -f D:\yuanjing\agent\project1\docker-compose.yml ps
powershell -ExecutionPolicy Bypass -File D:\yuanjing\agent\project1\scripts\verify_model.ps1
```

Expected: API healthy; model health returns configured and ready with `qwen3.5:9b`.

- [ ] **Step 4: Run the three-case real-model evaluation**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
powershell -ExecutionPolicy Bypass -File .\scripts\verify_real_model.ps1 -ExpectedModel 'qwen3.5:9b' -Repeats 3 -RequestTimeoutSeconds 120
& 'C:\Users\yuanjing\AppData\Local\Programs\Ollama\ollama.exe' ps
```

Expected: machine-readable JSON and Markdown report are produced; both production stages execute in any case that reaches actionable; failures remain visible rather than being replaced with template prose.

- [ ] **Step 5: Run one production API memo job against the active dataset**

Use `GET /api/v1/overview` for `active_dataset.id`, `POST /api/v1/decision-memo/generate`, poll `GET /api/v1/decision-memo/jobs/{job_id}` until `completed` or `failed`, then on completion fetch `GET /api/v1/decision-memo?dataset_version_id=...` and `GET /api/v1/traces/{memo_id}?page=1&page_size=100`.

Assert the memo and every trace use the same dataset version, `model_name == "qwen3.5:9b"`, `provider == "ollama"`, all evidence IDs resolve in the current version, and no trace contains prompts or response bodies. If the task fails, retain and report the stable `error_code`; do not claim success.

- [ ] **Step 6: Run the complete project regression**

Run:

```powershell
Set-Location D:\yuanjing\agent\project1
.\.venv\Scripts\python.exe -m pytest backend\tests -q
.\.venv\Scripts\python.exe -m ruff check --no-cache backend\signalforge backend\tests eval
Set-Location .\frontend
npm test -- --run
npm run lint
npm run build
Set-Location ..
.\.venv\Scripts\python.exe eval\run_eval.py
powershell -ExecutionPolicy Bypass -File .\scripts\verify_local.ps1
git -C D:\yuanjing\agent\project1 diff --check
```

Expected: backend and frontend tests, Ruff, ESLint, production build, refreshed offline evaluation, local verification, and diff checks all pass.

- [ ] **Step 7: Audit the report against measured facts**

Check that `docs/LOCAL_MODEL_EVAL_REPORT.md` names the exact digest and configuration, lists raw and system denominators, contains `manual_review_status: not_performed`, discloses synthetic cases, and does not call the results production quality or business impact. Update `docs/CASE_STUDY.md` only with measured values and preserve the distinction between offline synthetic regression and live local-model evaluation.
