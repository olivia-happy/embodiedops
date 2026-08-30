# SignalForge MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locally runnable Chinese-first AI decision-intelligence web application that turns versioned review and market-event evidence into explainable insight, decision, and risk cards.

**Architecture:** A Python ETL and FastAPI service own DuckDB, deterministic scoring, evidence validation, structured AI generation, and trace persistence. A Next.js application renders four workspaces through typed HTTP APIs. The app is useful without an LLM key through deterministic summaries; an OpenAI-compatible provider is an optional enhancement, never a runtime requirement.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, DuckDB, pandas, sentence-transformers, pytest; Next.js 15, TypeScript, Tailwind CSS, TanStack Query, Recharts, Vitest, Playwright; Docker Compose.

## Global Constraints

- Keep all work local. Do not configure Git identity, commit, create a remote, or push unless the user explicitly authorizes it.
- Default demo data is Chinese ASAP review data plus 10–20 manually curated authority-source market events; Steam remains an opt-in extension only.
- Every persisted AI claim must contain valid `evidence_ids` from the active dataset version; insufficient evidence must return a refusal state.
- All numerical cards are computed by DuckDB SQL or deterministic Python; LLM output must not be the source of a metric or priority score.
- Do not distribute data beyond its license. Store an approved small demo sample plus a data manifest and import script.
- UI copy and primary documentation are Chinese. No login, multi-tenancy, background crawling, autonomous actions, or investment advice.
- Each task includes its own test cycle. Run the stated commands from the repository root. Local commits are deliberately omitted because the user has not configured a Git identity.

---

## Planned File Structure

```text
backend/
  pyproject.toml
  signalforge/
    api/{app.py,deps.py,schemas.py,routers/*.py}
    core/{config.py,errors.py,models.py}
    db/{connection.py,schema.sql,repositories.py}
    etl/{manifest.py,normalize.py,load.py}
    services/{analytics.py,scoring.py,retrieval.py,generation.py,tracing.py}
  tests/{conftest.py,test_*.py}
frontend/
  package.json
  src/{app/*,components/*,lib/{api.ts,types.ts}}
  tests/*.test.tsx
data/{demo/,manifests/}
docs/{DATA_CARD.md,DECISION_RULES.md,EVAL_REPORT.md,CASE_STUDY.md,PROJECT_PLAN.md}
eval/{gold_reviews.jsonl,run_eval.py}
docker-compose.yml
README.md
```

## Task 1: Establish a runnable local workspace and domain contracts

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/signalforge/core/config.py`
- Create: `backend/signalforge/core/models.py`
- Create: `backend/signalforge/core/errors.py`
- Create: `backend/tests/conftest.py`
- Create: `backend/tests/test_models.py`
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `.env.example`, `.gitignore`, `docker-compose.yml`

**Interfaces:**
- Produces `DatasetVersion`, `Evidence`, `Insight`, `DecisionCard`, `MarketEvent`, `TraceRecord`, and `EvidenceInsufficientError` for all later tasks.
- Produces `Settings.from_env()` with `database_path`, `llm_base_url`, `llm_model`, and `llm_api_key`.

- [ ] **Step 1: Write failing domain-contract tests**

```python
from signalforge.core.models import DatasetVersion, Insight

def test_insight_requires_at_least_one_evidence_id() -> None:
    version = DatasetVersion(id="asap-demo-v1", source_name="ASAP", row_count=3)
    insight = Insight(
        id="ins-1", dataset_version_id=version.id, title="服务响应慢",
        claim="用户反复提及等候时间", evidence_ids=["review-1"], confidence=0.82,
    )
    assert insight.evidence_ids == ["review-1"]
```

- [ ] **Step 2: Run the test to verify the package is absent**

Run: `cd backend; uv run pytest tests/test_models.py -v`  
Expected: FAIL because `signalforge` cannot be imported.

- [ ] **Step 3: Add the minimal project configuration and models**

```toml
# backend/pyproject.toml
[project]
name = "signalforge"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["duckdb>=1.1", "fastapi>=0.115", "pandas>=2.2", "pydantic>=2.9", "pydantic-settings>=2.6", "uvicorn[standard]>=0.32"]

[dependency-groups]
dev = ["pytest>=8.3", "httpx>=0.28", "ruff>=0.8"]
```

```python
# backend/signalforge/core/models.py
from pydantic import BaseModel, Field

class DatasetVersion(BaseModel):
    id: str
    source_name: str
    row_count: int = Field(ge=0)

class Insight(BaseModel):
    id: str
    dataset_version_id: str
    title: str
    claim: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
```

- [ ] **Step 4: Add environment-safe configuration and ignore rules**

```python
# backend/signalforge/core/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_path: str = "../data/signalforge.duckdb"
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
```

Put only variable names in `.env.example`, including `LLM_BASE_URL=`, `LLM_MODEL=`, and `LLM_API_KEY=`. Add `.env`, `data/*.duckdb`, `node_modules/`, `.next/`, `.venv/`, and generated coverage files to `.gitignore`.

- [ ] **Step 5: Run the test and static checks**

Run: `cd backend; uv sync --group dev; uv run pytest tests/test_models.py -v; uv run ruff check signalforge tests`  
Expected: PASS and no lint diagnostics.

## Task 2: Create the versioned DuckDB schema and repositories

**Files:**
- Create: `backend/signalforge/db/schema.sql`
- Create: `backend/signalforge/db/connection.py`
- Create: `backend/signalforge/db/repositories.py`
- Create: `backend/tests/test_repositories.py`

**Interfaces:**
- Consumes: `DatasetVersion` and domain model IDs from Task 1.
- Produces: `Database`, `apply_schema()`, `insert_dataset_version()`, `get_evidence()`, `save_trace()`, and `save_feedback()`.

- [ ] **Step 1: Write failing repository tests**

```python
from signalforge.db.connection import Database
from signalforge.db.repositories import insert_dataset_version, get_evidence

def test_evidence_is_scoped_to_its_dataset_version(tmp_path) -> None:
    db = Database(tmp_path / "test.duckdb")
    db.apply_schema()
    insert_dataset_version(db, "v1", "ASAP", "sha256:x", 1)
    db.execute("INSERT INTO reviews VALUES ('r1','v1','服务太慢',1,'service','negative',false)")
    assert get_evidence(db, "v1", ["r1"])[0]["content"] == "服务太慢"
```

- [ ] **Step 2: Run the test to verify failure**

Run: `cd backend; uv run pytest tests/test_repositories.py::test_evidence_is_scoped_to_its_dataset_version -v`  
Expected: FAIL because the database layer is absent.

- [ ] **Step 3: Define explicit tables and the connection wrapper**

```sql
CREATE TABLE IF NOT EXISTS dataset_versions (
  id VARCHAR PRIMARY KEY, source_name VARCHAR NOT NULL, source_url VARCHAR,
  file_hash VARCHAR NOT NULL, row_count INTEGER NOT NULL, imported_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS reviews (
  id VARCHAR, dataset_version_id VARCHAR, content VARCHAR, rating INTEGER,
  aspect VARCHAR, sentiment VARCHAR, redacted BOOLEAN NOT NULL, PRIMARY KEY (id, dataset_version_id)
);
CREATE TABLE IF NOT EXISTS market_events (
  id VARCHAR PRIMARY KEY, dataset_version_id VARCHAR, source_url VARCHAR NOT NULL,
  published_on DATE NOT NULL, excerpt VARCHAR NOT NULL, event_type VARCHAR NOT NULL,
  industry VARCHAR NOT NULL, evidence_quality INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS insights (
  id VARCHAR PRIMARY KEY, dataset_version_id VARCHAR, payload JSON NOT NULL, status VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS decision_cards (
  id VARCHAR PRIMARY KEY, dataset_version_id VARCHAR, payload JSON NOT NULL, status VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
  id VARCHAR PRIMARY KEY, entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL,
  decision VARCHAR NOT NULL, reason VARCHAR, created_at TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS traces (
  id VARCHAR PRIMARY KEY, entity_type VARCHAR NOT NULL, entity_id VARCHAR NOT NULL,
  dataset_version_id VARCHAR NOT NULL, prompt_version VARCHAR NOT NULL,
  model_name VARCHAR, evidence_ids JSON NOT NULL, validation_status VARCHAR NOT NULL,
  latency_ms INTEGER NOT NULL, token_estimate INTEGER NOT NULL, created_at TIMESTAMP NOT NULL
);
```

```python
# backend/signalforge/db/connection.py
import duckdb
from pathlib import Path

class Database:
    def __init__(self, path: Path) -> None: self.connection = duckdb.connect(str(path))
    def execute(self, sql: str, params: tuple = ()): return self.connection.execute(sql, params)
    def apply_schema(self) -> None:
        self.connection.execute(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
```

- [ ] **Step 4: Implement parameterized repository functions**

```python
def get_evidence(db: Database, dataset_version_id: str, evidence_ids: list[str]) -> list[dict]:
    placeholders = ",".join("?" for _ in evidence_ids)
    rows = db.execute(
        f"SELECT id, content, rating, aspect, sentiment FROM reviews WHERE dataset_version_id=? AND id IN ({placeholders})",
        (dataset_version_id, *evidence_ids),
    ).fetchdf().to_dict("records")
    return rows
```

Implement the remaining repository writes with positional parameters, UUID IDs, and UTC timestamps; never accept raw SQL from HTTP requests.

- [ ] **Step 5: Run repository tests**

Run: `cd backend; uv run pytest tests/test_repositories.py -v`  
Expected: PASS.

## Task 3: Build reproducible import, manifest, cleaning, and demo data

**Files:**
- Create: `data/demo/asap_reviews_sample.csv`
- Create: `data/demo/market_events_sample.csv`
- Create: `data/manifests/asap-demo-v1.json`
- Create: `backend/signalforge/etl/normalize.py`
- Create: `backend/signalforge/etl/manifest.py`
- Create: `backend/signalforge/etl/load.py`
- Create: `backend/tests/test_etl.py`
- Create: `docs/DATA_CARD.md`

**Interfaces:**
- Consumes: Task 2 `Database` and `dataset_versions` repository functions.
- Produces: `load_snapshot(db, reviews_path, events_path, source_metadata) -> DatasetVersion` and normalized rows with safe `id`, `content`, `rating`, `aspect`, `sentiment`, and `redacted` fields.

- [ ] **Step 1: Write failing import tests with an explicit redaction case**

```python
from signalforge.etl.normalize import normalize_review

def test_normalize_review_redacts_phone_number() -> None:
    review = normalize_review({"review_id": "1", "review": "请联系 13812345678", "rating": "1"})
    assert review.content == "请联系 [PHONE]"
    assert review.redacted is True
    assert review.rating == 1
```

- [ ] **Step 2: Run the failing ETL test**

Run: `cd backend; uv run pytest tests/test_etl.py::test_normalize_review_redacts_phone_number -v`  
Expected: FAIL because `normalize_review` is absent.

- [ ] **Step 3: Implement conservative normalisation and manifest hashing**

```python
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

def normalize_review(raw: dict[str, str]) -> NormalizedReview:
    original = raw["review"].strip()
    content = PHONE.sub("[PHONE]", original)
    return NormalizedReview(
        id=f"asap-{raw['review_id']}", content=content, rating=int(raw["rating"]),
        aspect=raw.get("aspect", "unknown"), sentiment=raw.get("sentiment", "unknown"),
        redacted=content != original,
    )
```

Use SHA-256 over each imported file. Write a JSON manifest containing source URL, license, citation, import timestamp, hash, column mapping, and row count. Populate the demo review sample with only license-compliant rows and include at least service, environment, and food aspects. Populate the event sample with source URL, date, excerpt, type, industry, and evidence quality for 10–20 manually reviewed public items.

- [ ] **Step 4: Implement an idempotent loader**

```python
def load_snapshot(db: Database, reviews_path: Path, events_path: Path, metadata: SourceMetadata) -> DatasetVersion:
    version_id = f"{metadata.slug}-{file_sha256(reviews_path)[:12]}"
    if db.execute("SELECT 1 FROM dataset_versions WHERE id=?", (version_id,)).fetchone():
        return read_version(db, version_id)
    # normalize, insert version and rows in one transaction, then write manifest
```

Reject files lacking required columns and return a row-level error report. Do not replace an already successful version on a failed import.

- [ ] **Step 5: Run import tests and verify the manifest**

Run: `cd backend; uv run pytest tests/test_etl.py -v; uv run python -m signalforge.etl.load --reviews ../data/demo/asap_reviews_sample.csv --events ../data/demo/market_events_sample.csv`  
Expected: PASS; a manifest and one DuckDB dataset version are created.

## Task 4: Implement deterministic analytics and explainable scoring

**Files:**
- Create: `backend/signalforge/services/analytics.py`
- Create: `backend/signalforge/services/scoring.py`
- Create: `backend/tests/test_scoring.py`
- Create: `backend/tests/test_analytics.py`
- Create: `docs/DECISION_RULES.md`

**Interfaces:**
- Consumes: `Database`, `reviews`, and `market_events` from Tasks 2–3.
- Produces: `list_topic_metrics(version_id, filters) -> list[TopicMetric]`, `score_opportunity(metric) -> ScoreBreakdown`, and `score_risk(event) -> ScoreBreakdown`.

- [ ] **Step 1: Write scoring tests that expose the exact formula**

```python
from signalforge.services.scoring import OpportunityInputs, score_opportunity

def test_opportunity_score_has_explainable_contributions() -> None:
    score = score_opportunity(OpportunityInputs(affected=100, negativity=80, severity=70, business_fit=60, evidence=90))
    assert score.total == 82.0
    assert score.contributions["affected"] == 30.0
```

- [ ] **Step 2: Run the failing scoring test**

Run: `cd backend; uv run pytest tests/test_scoring.py::test_opportunity_score_has_explainable_contributions -v`  
Expected: FAIL because scoring is absent.

- [ ] **Step 3: Implement score types and SQL-backed metric aggregation**

```python
def score_opportunity(i: OpportunityInputs) -> ScoreBreakdown:
    contributions = {
        "affected": i.affected * 0.30, "negativity": i.negativity * 0.25,
        "severity": i.severity * 0.20, "business_fit": i.business_fit * 0.15,
        "evidence": i.evidence * 0.10,
    }
    return ScoreBreakdown(total=round(sum(contributions.values()), 1), contributions=contributions)
```

Aggregate review counts and negative proportions in DuckDB grouped by aspect. If a required value is unavailable, return `scoreable=False` and a list of missing fields; do not treat absent data as zero. Implement risk scoring with the fixed weights 0.35 impact, 0.30 urgency, 0.20 likelihood, and 0.15 evidence quality.

- [ ] **Step 4: Write the decision-rules document while code is fresh**

Document every input’s source, normalization, weight, limitation, and user-configurable business-fit field. Include one arithmetic worked example that matches `test_scoring.py`.

- [ ] **Step 5: Run analytics and scoring tests**

Run: `cd backend; uv run pytest tests/test_scoring.py tests/test_analytics.py -v`  
Expected: PASS with exact totals and SQL-derived counts.

## Task 5: Add evidence retrieval and safe structured generation

**Files:**
- Create: `backend/signalforge/services/retrieval.py`
- Create: `backend/signalforge/services/generation.py`
- Create: `backend/signalforge/services/tracing.py`
- Create: `backend/tests/test_retrieval.py`
- Create: `backend/tests/test_generation.py`

**Interfaces:**
- Consumes: Task 2 `get_evidence()`, Task 4 topic metrics, and Task 1 `Insight`.
- Produces: `retrieve_evidence(version_id, query, limit=8) -> list[Evidence]`, `generate_insight(request) -> GeneratedInsight`, `validate_generated_insight(result, evidence) -> ValidationResult`, and `TraceRecord` persistence.

- [ ] **Step 1: Write failing tests for refusal and fabricated citations**

```python
from signalforge.services.generation import validate_generated_insight

def test_generated_claim_with_unknown_evidence_id_is_rejected() -> None:
    result = {"claim": "服务问题普遍", "evidence_ids": ["missing"], "confidence": 0.9, "unknowns": []}
    validation = validate_generated_insight(result, allowed_evidence_ids={"r1"})
    assert validation.accepted is False
    assert validation.reason == "unknown_evidence_id"
```

- [ ] **Step 2: Run the failing generation test**

Run: `cd backend; uv run pytest tests/test_generation.py::test_generated_claim_with_unknown_evidence_id_is_rejected -v`  
Expected: FAIL because validation is absent.

- [ ] **Step 3: Implement local retrieval before adding embeddings**

```python
def retrieve_evidence(db: Database, version_id: str, query: str, limit: int = 8) -> list[Evidence]:
    return keyword_ranked_reviews(db, version_id=version_id, query=query, limit=limit)
```

Add a `SentenceTransformerRetriever` behind the same interface only after the keyword implementation and test suite pass. It must run over the active DuckDB snapshot and return review IDs, text, aspect, sentiment, and relevance score. Use an embedding model configurable by environment variable and keep a keyword fallback for offline environments.

- [ ] **Step 4: Implement a strict provider adapter and deterministic fallback**

```python
class GeneratedInsight(BaseModel):
    claim: str
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    unknowns: list[str]
    recommended_action: Literal["investigate", "experiment", "monitor"]

def generate_insight(request: GenerateInsightRequest) -> GeneratedInsight | Refusal:
    if len(request.evidence) < 2:
        return Refusal(code="INSUFFICIENT_EVIDENCE", message="证据不足，建议补充样本或调整筛选条件。")
    return configured_provider_or_deterministic_summary(request)
```

The provider request must state that it may cite only supplied IDs and must return JSON matching `GeneratedInsight`. Validate the parsed result against allowed IDs before saving. Persist a trace for accepted, refused, invalid, retried, and fallback outcomes with no raw API key or unnecessary original prompt content.

- [ ] **Step 5: Run retrieval and generation tests**

Run: `cd backend; uv run pytest tests/test_retrieval.py tests/test_generation.py -v`  
Expected: PASS; unknown citations are rejected and two-evidence minimum is enforced.

## Task 6: Expose a typed FastAPI surface with failure states

**Files:**
- Create: `backend/signalforge/api/app.py`
- Create: `backend/signalforge/api/deps.py`
- Create: `backend/signalforge/api/schemas.py`
- Create: `backend/signalforge/api/routers/overview.py`
- Create: `backend/signalforge/api/routers/insights.py`
- Create: `backend/signalforge/api/routers/decisions.py`
- Create: `backend/signalforge/api/routers/risks.py`
- Create: `backend/signalforge/api/routers/traces.py`
- Create: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: services from Tasks 3–5.
- Produces the JSON HTTP contract used by Task 7: `GET /api/v1/overview`, `GET /api/v1/insights`, `POST /api/v1/insights/generate`, `POST /api/v1/decisions`, `POST /api/v1/feedback`, `GET /api/v1/risks`, and `GET /api/v1/traces/{entity_id}`.

- [ ] **Step 1: Write a failing API test for evidence refusal**

```python
def test_generate_insight_returns_422_for_insufficient_evidence(client) -> None:
    response = client.post("/api/v1/insights/generate", json={"dataset_version_id": "v1", "query": "服务"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INSUFFICIENT_EVIDENCE"
```

- [ ] **Step 2: Run the failing route test**

Run: `cd backend; uv run pytest tests/test_api.py::test_generate_insight_returns_422_for_insufficient_evidence -v`  
Expected: FAIL because the application is absent.

- [ ] **Step 3: Implement application wiring and error envelopes**

```python
app = FastAPI(title="SignalForge API", version="0.1.0")

@app.exception_handler(EvidenceInsufficientError)
async def evidence_error(_, exc: EvidenceInsufficientError):
    return JSONResponse(status_code=422, content={"detail": {"code": "INSUFFICIENT_EVIDENCE", "message": str(exc)}})
```

Use Pydantic request models and dependency-injected `Database`; validate all filter enums and pagination limits. `GET /api/v1/overview` returns active dataset metadata, three opportunity cards, two risks, and summary metrics. Feedback accepts only `confirmed`, `rejected`, or `edited` decisions with an optional 500-character reason.

- [ ] **Step 4: Add health and data-freshness responses**

Implement `GET /healthz` with database readiness. Include `imported_at` and `is_stale` in every overview response; stale means older than the configured 30-day demo threshold and is a display warning, not an error.

- [ ] **Step 5: Run API tests and start the service**

Run: `cd backend; uv run pytest tests/test_api.py -v; uv run uvicorn signalforge.api.app:app --reload --port 8000`  
Expected: PASS; `/docs` exposes the typed endpoints and invalid claims never persist.

## Task 7: Build the Next.js shell, typed client, and overview workspace

**Files:**
- Create: `frontend/src/app/layout.tsx`
- Create: `frontend/src/app/page.tsx`
- Create: `frontend/src/app/globals.css`
- Create: `frontend/src/lib/types.ts`
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/components/navigation.tsx`
- Create: `frontend/src/components/metric-card.tsx`
- Create: `frontend/src/components/opportunity-card.tsx`
- Create: `frontend/src/components/risk-card.tsx`
- Create: `frontend/tests/overview.test.tsx`

**Interfaces:**
- Consumes: Task 6 `OverviewResponse` JSON.
- Produces: Chinese dashboard layout and reusable cards used in Tasks 8–9.

- [ ] **Step 1: Write a failing overview UI test**

```tsx
it("renders a score and opens its evidence affordance", async () => {
  render(<OpportunityCard card={{ id: "d1", title: "服务慢", score: 82.5, evidenceCount: 8 }} />)
  expect(screen.getByText("82.5")).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "查看证据" })).toBeEnabled()
})
```

- [ ] **Step 2: Run the failing frontend test**

Run: `cd frontend; npm test -- overview.test.tsx`  
Expected: FAIL because the component is absent.

- [ ] **Step 3: Create the app shell and typed API client**

```ts
export async function getOverview(): Promise<OverviewResponse> {
  const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}/api/v1/overview`, { cache: "no-store" })
  if (!response.ok) throw new Error("无法加载决策总览")
  return response.json() as Promise<OverviewResponse>
}
```

Create desktop-first responsive navigation for `决策总览`, `用户洞察`, `决策实验室`, and `产业风险雷达`. Add a visible data-version badge and stale-data warning. Use neutral professional styling and no copied branding from reference repositories.

- [ ] **Step 4: Implement overview loading, empty, and failure states**

Render summary metrics, three opportunity cards, two risk cards, and a local-time snapshot label. Provide a clear no-data state with the exact instruction to run the importer. On fetch failure, show the error and a retry button; do not substitute fabricated numbers.

- [ ] **Step 5: Run frontend unit checks**

Run: `cd frontend; npm install; npm test -- overview.test.tsx; npm run lint; npm run build`  
Expected: PASS; the production build contains no TypeScript errors.

## Task 8: Implement the insight workbench and evidence drawer

**Files:**
- Create: `frontend/src/app/insights/page.tsx`
- Create: `frontend/src/components/insight-filters.tsx`
- Create: `frontend/src/components/topic-chart.tsx`
- Create: `frontend/src/components/evidence-drawer.tsx`
- Create: `frontend/src/components/generation-panel.tsx`
- Create: `frontend/tests/insights.test.tsx`

**Interfaces:**
- Consumes: Task 6 insight list and generation endpoints, including `INSUFFICIENT_EVIDENCE` errors.
- Produces: selected `Insight` and evidence IDs for Task 9 decision-card creation.

- [ ] **Step 1: Write a failing test for transparent evidence display**

```tsx
it("shows original evidence instead of only an AI claim", async () => {
  render(<EvidenceDrawer open evidence={[{ id: "r1", content: "等位太久", sentiment: "negative" }]} />)
  expect(screen.getByText("等位太久")).toBeVisible()
  expect(screen.getByText("r1")).toBeVisible()
})
```

- [ ] **Step 2: Run the failing insight test**

Run: `cd frontend; npm test -- insights.test.tsx`  
Expected: FAIL because the drawer is absent.

- [ ] **Step 3: Build filters and chart from server-supplied data**

Use only backend-returned aspect, sentiment, and rating filters. Draw topic volume/negative-rate with Recharts, include a textual table fallback, and make chart tooltips show sample size. Keep filters in URL search parameters so an interviewer can share a reproducible view.

- [ ] **Step 4: Build generation and refusal experiences**

```tsx
if (error?.code === "INSUFFICIENT_EVIDENCE") {
  return <Alert title="暂不生成结论" description="证据不足，建议扩大样本或调整筛选条件。" />
}
```

Show claim, confidence, unknowns, action type, and evidence count only after a successful structured response. The evidence drawer must show raw text, labels, relevance, and the active data version.

- [ ] **Step 5: Run workbench tests and manually inspect filters**

Run: `cd frontend; npm test -- insights.test.tsx; npm run build`  
Expected: PASS; changing a filter changes both the chart and evidence query.

## Task 9: Build decision laboratory, feedback loop, risk radar, and trace drawer

**Files:**
- Create: `frontend/src/app/decisions/page.tsx`
- Create: `frontend/src/app/risks/page.tsx`
- Create: `frontend/src/components/score-breakdown.tsx`
- Create: `frontend/src/components/decision-form.tsx`
- Create: `frontend/src/components/feedback-controls.tsx`
- Create: `frontend/src/components/risk-timeline.tsx`
- Create: `frontend/src/components/trace-drawer.tsx`
- Create: `frontend/tests/decisions.test.tsx`
- Create: `frontend/tests/risks.test.tsx`

**Interfaces:**
- Consumes: Task 6 decision, feedback, risk, and trace endpoints; Task 8 selected insight.
- Produces: persisted decision cards and human feedback records linked to data version and trace.

- [ ] **Step 1: Write failing tests for score transparency and feedback**

```tsx
it("renders all opportunity-score contributions", () => {
  render(<ScoreBreakdown contributions={{ affected: 30, negativity: 20, severity: 14, business_fit: 9, evidence: 9 }} total={82} />)
  expect(screen.getByText("受影响度 30.0")).toBeVisible()
  expect(screen.getByText("82.0 / 100")).toBeVisible()
})
```

- [ ] **Step 2: Run the failing decision test**

Run: `cd frontend; npm test -- decisions.test.tsx`  
Expected: FAIL because the score component is absent.

- [ ] **Step 3: Implement decision cards and explicit human choices**

The decision form requires title, evidence IDs, problem statement, hypothesis, primary metric, guardrail metric, owner, and due date. Feedback buttons submit only `confirmed`, `rejected`, or `edited`; the edited flow requires a written reason. Render `scoreable=false` cards as “待补充数据” and omit a misleading rank.

- [ ] **Step 4: Implement market risk radar and trace drawer**

Display event source link, date, type, industry, score breakdown, mitigation action, owner, and status in a timeline plus sortable table. The trace drawer displays data version, prompt version, model name, validation status, latency, token estimate, and evidence IDs. It must never display an API key or hidden chain-of-thought.

- [ ] **Step 5: Run workspace tests and build**

Run: `cd frontend; npm test -- decisions.test.tsx risks.test.tsx; npm run build`  
Expected: PASS; a decision can be confirmed/rejected and a risk card links to its authority source.

## Task 10: Add gold-set evaluation, regression suite, and end-to-end checks

**Files:**
- Create: `eval/gold_reviews.jsonl`
- Create: `eval/run_eval.py`
- Create: `backend/tests/test_regression_questions.py`
- Create: `frontend/e2e/decision-flow.spec.ts`
- Create: `docs/EVAL_REPORT.md`
- Modify: `backend/pyproject.toml`
- Modify: `frontend/package.json`

**Interfaces:**
- Consumes: Task 3 snapshots, Task 4 scoring, and Task 5 retrieval/generation contracts.
- Produces: a reproducible JSON evaluation report and a documented set of measured, not assumed, quality results.

- [ ] **Step 1: Create failing regression tests for no-evidence refusal**

```python
@pytest.mark.parametrize("query", ["不存在的罕见问题", "无法由当前样本回答的因果关系"])
def test_unsupported_questions_refuse_generation(service, query: str) -> None:
    result = service.generate(query=query, dataset_version_id="asap-demo-v1")
    assert result.code == "INSUFFICIENT_EVIDENCE"
```

- [ ] **Step 2: Run regression tests to verify failure before fixture setup**

Run: `cd backend; uv run pytest tests/test_regression_questions.py -v`  
Expected: FAIL until the service fixture and refusal handling are wired.

- [ ] **Step 3: Author the gold set and evaluator**

Each JSONL row must contain `review_id`, `text`, `gold_aspect`, `gold_sentiment`, `gold_severity`, and `supports_claim`. Include approximately 80 rows across positive, neutral, negative, ambiguous, and redacted examples. `run_eval.py` must calculate macro-F1 for aspect/sentiment, evidence precision, unsupported-claim rate, and acceptance/edited/rejected feedback distribution; write results to `eval/results.json`.

- [ ] **Step 4: Add a browser-level critical-flow test**

```ts
test("creates an evidence-backed decision", async ({ page }) => {
  await page.goto("/insights?aspect=service&sentiment=negative")
  await page.getByRole("button", { name: "生成洞察" }).click()
  await page.getByRole("button", { name: "创建行动卡" }).click()
  await expect(page.getByText("评分拆解")).toBeVisible()
  await expect(page.getByRole("button", { name: "查看证据" })).toBeVisible()
})
```

- [ ] **Step 5: Run all backend, evaluator, and E2E tests**

Run: `cd backend; uv run pytest -v; cd ..; python eval/run_eval.py; cd frontend; npx playwright test`  
Expected: PASS; `eval/results.json` exists and the report documents both successes and failures.

## Task 11: Package the local demonstration and documentation

**Files:**
- Create: `README.md`
- Create: `docs/PRD.md`
- Create: `docs/CASE_STUDY.md`
- Create: `docs/PROJECT_PLAN.md`
- Create: `assets/demo.gif`
- Create: `scripts/verify_local.ps1`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: the completed application and measured output from Tasks 1–10.
- Produces: a clean-environment startup path, a three-minute demo narrative, and all local review artifacts.

- [ ] **Step 1: Write a failing local verification script check**

```powershell
$health = Invoke-RestMethod http://localhost:8000/healthz
if ($health.status -ne 'ok') { throw 'API health check failed' }
if (-not (Test-Path 'eval/results.json')) { throw 'Evaluation results are missing' }
```

- [ ] **Step 2: Run the check before containers and app are configured**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify_local.ps1`  
Expected: FAIL until the API, importer, and evaluation output exist.

- [ ] **Step 3: Write docs from actual measured behavior**

README must include: one-sentence value proposition, local quickstart, architecture image/diagram, data provenance, screenshots/GIF, a link to `docs/EVAL_REPORT.md`, known limitations, and the three role-specific interview narratives. `CASE_STUDY.md` follows one real sample from review evidence to score, experiment card, feedback, and trace. `PRD.md` explains user/problem/scope. `PROJECT_PLAN.md` records timeline, risks, decisions, and retrospective.

- [ ] **Step 4: Create Docker Compose and repeatable local verification**

```yaml
services:
  api:
    build: ./backend
    ports: ["8000:8000"]
    volumes: ["./data:/app/data"]
  web:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      NEXT_PUBLIC_API_BASE_URL: http://localhost:8000
```

Document separate development and Docker commands. Record the demo GIF only after the application displays actual sample data and a real evidence drawer; do not use mock screenshots in place of the product.

- [ ] **Step 5: Verify from a clean local startup**

Run: `docker compose up --build -d; powershell -ExecutionPolicy Bypass -File scripts/verify_local.ps1; docker compose down`  
Expected: PASS; the API is healthy, evaluation artifact exists, and the app runs without LLM credentials.

## Plan Self-Review

### Spec coverage

| Specification requirement | Implementing task(s) |
|---|---|
| Chinese primary data, licensed demo sample, versioned source metadata | 3, 11 |
| Authority-source market event radar | 3, 4, 6, 9 |
| Four named workspaces | 7, 8, 9 |
| DuckDB, SQL reproducibility, deterministic scores | 2, 4, 6 |
| Evidence-bound structured AI and explicit refusal | 5, 6, 8 |
| Human confirmation/rejection, trace and prompt/model metadata | 2, 5, 6, 9 |
| Failure handling: stale data, import, LLM, sensitive text, unsafe SQL | 2, 3, 5, 6, 7 |
| Gold set, baseline/quality reporting, regression and E2E tests | 10 |
| README, PRD, data card, case study, local demo and no GitHub action | 1, 3, 4, 11 |

No specification requirement is intentionally excluded. The optional semantic embedding and model provider are behind stable interfaces so the application remains usable offline.

### Placeholder scan

The plan has no unresolved scope markers or unnamed future implementation. Each service, endpoint, status, formula, data boundary, and test command is named above.

### Type consistency

`DatasetVersion.id` is the `dataset_version_id` used by evidence, insight, decision, risk, and trace records. `evidence_ids` is required on `Insight` and `GeneratedInsight`, validated by Task 5, returned by Task 6, rendered by Task 8, and preserved in Task 9/10. `ScoreBreakdown` is produced in Task 4 and consumed by the decision/risk views in Task 9.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-09-signalforge-mvp.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task and review between tasks.
2. **Inline Execution** — execute tasks in this session using executing-plans, in batches with checkpoints.

Choose one approach before any implementation begins.
