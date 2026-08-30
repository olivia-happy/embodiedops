# Interview Demo Tunnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose a temporary HTTPS, read-only SignalForge demo link through Cloudflare Quick Tunnel without exposing the API, Ollama, database, or write operations.

**Architecture:** The browser uses same-origin `/api/*` requests. A Next.js catch-all route proxies only approved API paths to a fixed `SIGNALFORGE_API_ORIGIN`; Docker sets that origin to the internal `api:8000` service. A `DEMO_READ_ONLY` setting makes the backend reject memo-generation POSTs with `DEMO_READ_ONLY`, while the frontend hides generation and experiment-write CTAs. PowerShell scripts start and stop one precisely tracked `cloudflared` process targeting only `http://localhost:3000`.

**Tech Stack:** Next.js 15 App Router, TypeScript, FastAPI, Pydantic Settings, Docker Compose, PowerShell, Vitest, pytest, Cloudflare `cloudflared` Quick Tunnel.

## Global Constraints

- Only the frontend port `3000` is tunneled; ports `8000` and `11434` and `data/signalforge.duckdb` remain private.
- `DEMO_READ_ONLY` defaults to `false` for local development and must be `true` for the public demo.
- `DEMO_READ_ONLY=true` rejects `POST /api/v1/decision-memo/generate` with HTTP 403 and stable code `DEMO_READ_ONLY`; it must not create a job or start the manager.
- The browser must never default to `http://localhost:8000`; the client API base defaults to the empty string and uses same-origin proxying.
- The proxy target is a fixed local/container origin from `SIGNALFORGE_API_ORIGIN`; user input cannot choose a host or scheme.
- Tunnel scripts must not install software, stop unrelated processes, expose Ollama, or print secrets; they return non-zero when `cloudflared` or the local web service is unavailable.
- Quick Tunnel is a temporary testing/interview channel, not production hosting; no paid provider, API key, or cloud model is introduced.
- This repository has an unborn `main` branch with no `HEAD`; do not fabricate commits or push. Each task ends with its test evidence and `git diff --check`.

---

### Task 1: Same-origin API proxy

**Files:**
- Create: `frontend/src/app/api/[...path]/route.ts`
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/Dockerfile`
- Modify: `docker-compose.yml`
- Test: `frontend/tests/api-proxy.test.ts`

**Interfaces:**
- Consumes: browser requests such as `/api/v1/overview`, `/api/v1/evidence?...`, and `/healthz/model`.
- Produces: `GET|HEAD|POST|OPTIONS` proxy handlers that return the upstream status, content type, and body without exposing the upstream host.

- [ ] **Step 1: Write the failing client and proxy tests**

Add tests asserting that `getOverview()` calls `/api/v1/overview` when `NEXT_PUBLIC_API_BASE_URL` is absent, and that the route rejects an unapproved path while forwarding an approved path to the configured origin without following a user-supplied absolute URL.

- [ ] **Step 2: Run the focused tests and verify the red state**

Run from `D:\yuanjing\agent\project1\frontend`:

```powershell
npm test -- api-proxy.test.ts api.test.ts
```

Expected: the client still calls `http://localhost:8000` and the proxy module is missing.

- [ ] **Step 3: Implement the fixed-origin proxy**

Use `process.env.SIGNALFORGE_API_ORIGIN ?? "http://localhost:8000"`, parse the incoming catch-all segments, allow only `v1` and `healthz` roots, and construct the upstream URL with `new URL(path, origin)` plus the original query string. Forward only request headers needed for content negotiation and the body for methods that permit one. Return a 404 JSON error for disallowed paths and never concatenate a user-provided scheme or host.

Change the client default to:

```ts
const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";
```

Set the frontend build default to empty and set Docker's server-side `SIGNALFORGE_API_ORIGIN` to `http://api:8000`; local development continues to use the localhost fallback.

- [ ] **Step 4: Run the focused tests and verify the green state**

Run the same Vitest command. Expected: all proxy and batching tests pass, including query preservation and host-injection rejection.

- [ ] **Step 5: Record the task checkpoint**

Run `git diff --check` and record the passing test command; no commit is attempted because the repository has no `HEAD`.

### Task 2: Backend read-only demo gate

**Files:**
- Modify: `backend/signalforge/core/config.py`
- Modify: `backend/signalforge/api/routers/decision_memos.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Test: `backend/tests/test_demo_read_only.py`

**Interfaces:**
- Consumes: `Settings.demo_read_only: bool` via `Depends(get_settings)`.
- Produces: HTTP 403 JSON `{"detail":{"code":"DEMO_READ_ONLY",...}}` before `create_memo_job()` or `MemoJobManager.start()` executes.

- [ ] **Step 1: Write the failing backend tests**

Cover the default `false`, environment parsing of `DEMO_READ_ONLY=true`, rejection of generation with a mocked manager whose `start` must not be called, and preservation of GET memo/job reads.

- [ ] **Step 2: Run the focused tests and verify the red state**

Run:

```powershell
cd D:\yuanjing\agent\project1
.venv\Scripts\python.exe -m pytest backend/tests/test_demo_read_only.py -q
```

Expected: `Settings` has no `demo_read_only` field and the POST currently creates a job.

- [ ] **Step 3: Implement the minimal gate and configuration**

Add `demo_read_only: bool = False` to `Settings`. Add `DEMO_READ_ONLY: ${DEMO_READ_ONLY:-false}` to the API service environment. In `generate_decision_memo_job`, resolve settings before creating the requested job and raise:

```python
raise HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail={"code": "DEMO_READ_ONLY", "message": "演示模式为只读"},
)
```

Document `DEMO_READ_ONLY=false` in `.env.example`; the tunnel launcher will require the public/demo compose environment to set it true.

- [ ] **Step 4: Run focused tests and the router regression**

Run:

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_demo_read_only.py backend/tests/test_api.py -q
```

Expected: all pass and no new job row appears in the read-only test database.

- [ ] **Step 5: Record the task checkpoint**

Run Ruff on the modified Python files and `git diff --check`; record results without committing.

### Task 3: Frontend read-only presentation

**Files:**
- Modify: `frontend/src/components/decision-memo-panel.tsx`
- Modify: `frontend/src/app/page.tsx`
- Modify: `frontend/src/app/decisions/page.tsx`
- Modify: `frontend/Dockerfile`
- Modify: `docker-compose.yml`
- Test: `frontend/tests/demo-read-only.test.tsx`

**Interfaces:**
- Consumes: compile-time `NEXT_PUBLIC_DEMO_READ_ONLY` and the existing memo/job/health props.
- Produces: a visible “面试演示 · 只读” label; no generate/retry/create-experiment CTA when true; evidence and navigation remain usable.

- [ ] **Step 1: Write the failing UI tests**

Render the memo panel with `NEXT_PUBLIC_DEMO_READ_ONLY=true` (or an injected `readOnly` prop) and assert that generation, retry, and experiment controls are absent while evidence buttons remain. Assert the normal local mode still renders generation controls.

- [ ] **Step 2: Run the focused tests and verify the red state**

Run:

```powershell
cd D:\yuanjing\agent\project1\frontend
npm test -- demo-read-only.test.tsx
```

Expected: the existing buttons are still rendered in demo mode.

- [ ] **Step 3: Implement the demo-mode presentation**

Add a small `readOnly` prop derived from `process.env.NEXT_PUBLIC_DEMO_READ_ONLY === "true"`; guard the generate/retry anchors and buttons, replace them with a non-actionable status message, and leave GET evidence interactions intact. Add the build arg `NEXT_PUBLIC_DEMO_READ_ONLY: ${DEMO_READ_ONLY:-false}` so the image and backend use the same mode.

- [ ] **Step 4: Run focused UI tests**

Run the same Vitest command and verify both read-only and local-editable assertions pass.

- [ ] **Step 5: Record the task checkpoint**

Run ESLint on the touched frontend files and `git diff --check`.

### Task 4: Start/stop Quick Tunnel scripts and documentation

**Files:**
- Create: `scripts/start_demo_tunnel.ps1`
- Create: `scripts/stop_demo_tunnel.ps1`
- Modify: `README.md`
- Test: `backend/tests/test_demo_tunnel_scripts.py`

**Interfaces:**
- `start_demo_tunnel.ps1`: validates `http://localhost:3000`, resolves `cloudflared`, starts exactly one hidden `cloudflared tunnel --url http://localhost:3000 --no-autoupdate`, waits for a `https://*.trycloudflare.com` line, writes one PID file, and prints only the URL/PID.
- `stop_demo_tunnel.ps1`: reads the PID file, verifies the process executable is `cloudflared`, stops only that PID, removes the PID file, and is idempotent.

- [ ] **Step 1: Write script contract tests**

Use a fake `cloudflared` executable and a temporary PID directory to test missing executable, unavailable web port, URL extraction, stale PID, wrong executable, exact-process stop, idempotent stop, and non-zero failure output. Do not start a real tunnel in the normal test suite.

- [ ] **Step 2: Run the script tests and parser before implementation**

Run the focused pytest file and PowerShell parser. Expected: script files are missing and tests fail.

- [ ] **Step 3: Implement the guarded scripts**

Use `Start-Process -WindowStyle Hidden -PassThru`; do not use broad `Get-Process cloudflared | Stop-Process`. Store only the PID and a private temporary log path. Parse the random Quick Tunnel URL with a strict regex and time out without printing a fake URL.

- [ ] **Step 4: Document the interview workflow**

Add commands to README: set `DEMO_READ_ONLY=true`, rebuild/start the compose stack, run the start script, copy the URL, and stop it afterward. Explicitly state that Quick Tunnel is for a short interview demo, that the data must be non-sensitive, and that no Ollama/API endpoint is shared.

- [ ] **Step 5: Run focused tests and parser**

Run the script tests, `Parser::ParseFile()` for both scripts, and `git diff --check`. If `cloudflared` is not installed, report that as an external prerequisite; do not download it automatically.

### Task 5: Integrated demo acceptance

**Files:**
- Modify: `scripts/verify_local.ps1`
- Test: `backend/tests/test_demo_integration.py`

- [ ] **Step 1: Add integration assertions**

Against the local Docker stack, assert web status 200, same-origin `/api/v1/overview` status 200, demo-mode generation returns 403 `DEMO_READ_ONLY`, a rejected request leaves job count unchanged, and the API response never contains the internal `api:8000` or `11434` endpoint.

- [ ] **Step 2: Run the integrated local checks**

Run the backend/frontend full suites, Ruff, ESLint, Next production build, PowerShell parser, and `scripts/verify_local.ps1`. Do not run the expensive real-model verifier or automatically open a public tunnel.

- [ ] **Step 3: Verify the public-link prerequisite without exposing it**

Run `Get-Command cloudflared` and report whether the executable is available. If present, perform a manual start/stop smoke test only after the user explicitly asks to publish the link; otherwise leave the stack local and document the exact install prerequisite.

- [ ] **Step 4: Final checkpoint**

Run `git diff --check`, report all test counts, and preserve the workspace. Because `main` has no `HEAD`, do not commit, merge, push, or delete files.
